package edu.kit.kastel.mcse.ardoco.sandman.inconsistency.ardoco;

import edu.kit.kastel.mcse.ardoco.core.api.models.ModelFormat;
import edu.kit.kastel.mcse.ardoco.core.api.text.Text;
import edu.kit.kastel.mcse.ardoco.core.textproviderjson.dto.TextDto;
import edu.kit.kastel.mcse.ardoco.sandman.agent.Ids;
import edu.kit.kastel.mcse.ardoco.sandman.ardocotext.CorenlpClient;
import edu.kit.kastel.mcse.ardoco.sandman.ardocotext.SentenceRendering;
import edu.kit.kastel.mcse.ardoco.sandman.inconsistency.DetectionScope;
import edu.kit.kastel.mcse.ardoco.sandman.inconsistency.DetectorContext;
import edu.kit.kastel.mcse.ardoco.sandman.inconsistency.Finding;
import edu.kit.kastel.mcse.ardoco.sandman.inconsistency.InconsistencyDetector;
import java.io.IOException;
import java.io.UncheckedIOException;
import java.lang.management.ManagementFactory;
import java.lang.management.MemoryPoolMXBean;
import java.lang.management.MemoryType;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.CancellationException;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicLong;
import java.util.function.Supplier;
import java.util.stream.Stream;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * ArDoCo inconsistency detection (ADR-0015 L2, T3.1): one run per current document and model version, fed with the
 * active trace links of the configured approaches. Findings: see {@link Mapping}.
 */
public final class ArdocoIdDetector implements InconsistencyDetector {

    public static final String ID = "ardoco-id";
    /** Raise the first part when the mapping of ArDoCo results to findings changes. */
    public static final String VERSION = "0.1.0+0d1f22b";

    private static final Logger LOG = LoggerFactory.getLogger(ArdocoIdDetector.class);
    private static final Map<String, ModelFormat> FORMATS =
            Map.of("pcm-repository", ModelFormat.PCM, "uml", ModelFormat.UML);

    private record Cached(String stats, Mapping.Raw raw) {
        String summary(KgInput.Version doc, KgInput.Version model) {
            return "doc " + doc.path() + " x model " + model.path() + ": " + stats + ", "
                    + raw.models().size() + " model and " + raw.words().size() + " text findings";
        }
    }

    /**
     * Raw result of a run by everything the run reads: text, CoreNLP identity, model content, links. ArDoCo is
     * deterministic for these inputs (T3.3), so an unchanged pair of the next detection call skips its run. Bounded
     * LRU per JVM; a restart or a second replica starts empty.
     */
    private static final Map<String, Cached> CACHE = Collections.synchronizedMap(new LinkedHashMap<>(16, 0.75f, true) {
        @Override
        protected boolean removeEldestEntry(Map.Entry<String, Cached> eldest) {
            return size() > 4096;
        }
    });

    private static final AtomicLong RUNS = new AtomicLong();

    /** Number of ArDoCo runs in this JVM, without the pairs that the cache answered. */
    public static long runs() {
        return RUNS.get();
    }

    /** ServiceLoader entry: CoreNLP settings are read from the environment when the first run needs them. */
    public ArdocoIdDetector() {}

    @Override
    public String id() {
        return ID;
    }

    @Override
    public String version() {
        return VERSION;
    }

    @Override
    public Set<String> types() {
        return Set.of(Mapping.UNDOCUMENTED, Mapping.MISSING);
    }

    @Override
    public Set<String> paramKeys() {
        return Set.of("textKinds", "modelKinds", "linkApproaches");
    }

    @Override
    public ScopeMode scopeMode() {
        return ScopeMode.WHOLE_PROJECT;
    }

    @Override
    public String description() {
        return "ArDoCo inconsistency detection: undocumented model elements and text entities missing from the model";
    }

    @Override
    public String configIdentity(DetectorContext ctx) {
        return CorenlpClient.fromEnv().provider(ctx.cancelled()).identity();
    }

    @Override
    public List<Finding> detect(DetectionScope scope, DetectorContext ctx) {
        return detectWithWarnings(scope, ctx).findings();
    }

    @Override
    public Detection detectWithWarnings(DetectionScope scope, DetectorContext ctx) {
        List<String> textKinds = strings(ctx.params().get("textKinds"), List.of("architecture-doc"));
        List<String> modelKinds = strings(ctx.params().get("modelKinds"), List.of("architecture-model"));
        Object approachesParam = ctx.params().getOrDefault("linkApproaches", "all");
        List<String> approaches = "all".equals(approachesParam) ? null : strings(approachesParam, List.of());
        if (ctx.artifacts() == null) {
            throw new IllegalStateException("no artifact store configured");
        }
        KgInput kg = new KgInput(ctx.kg());
        List<String> warnings = new ArrayList<>();
        List<KgInput.Version> docs = kg.currentVersions(scope.projectId(), textKinds);
        List<KgInput.Version> models = kg.currentVersions(scope.projectId(), modelKinds);
        CorenlpClient corenlp = CorenlpClient.fromEnv();
        CorenlpClient.Provider provider = null;
        List<Mapping.RunResult> results = new ArrayList<>();
        for (KgInput.Version doc : docs) {
            List<KgInput.Sentence> sentences = kg.sentences(doc.id());
            if (sentences.isEmpty()) {
                warnings.add("document version " + doc.id() + " has no sentences, skipped");
                continue;
            }
            TextDto[] annotated = new TextDto[1];
            List<String> lines = null;
            for (KgInput.Version model : models) {
                ModelFormat format = format(model);
                if (format == null) {
                    warnings.add("model " + model.path() + " has unsupported format " + model.format() + ", skipped");
                    continue;
                }
                cancelCheck(ctx);
                if (lines == null) {
                    for (int i = 0; i < sentences.size(); i++) {
                        var s = sentences.get(i);
                        SentenceRendering.assertHash(i, doc.id(), s.localKey(), s.text());
                    }
                    lines = sentences.stream()
                            .map(s -> SentenceRendering.line(s.text()))
                            .toList();
                }
                if (provider == null) {
                    provider = corenlp.provider(ctx.cancelled());
                    LOG.info("ardoco-id: CoreNLP {}", provider.identity());
                }
                var textLines = lines;
                var textProvider = provider;
                Supplier<TextDto> annotation = () -> {
                    if (annotated[0] == null) {
                        annotated[0] = corenlp.annotateDto(textLines, textProvider, ctx.cancelled());
                    }
                    return annotated[0];
                };
                String textKey = provider.identity() + '\0' + String.join("\n", lines);
                results.add(runPair(ctx, kg, doc, sentences, annotation, textKey, model, format, approaches, warnings));
            }
        }
        return new Detection(Mapping.map(results), warnings);
    }

    private static Mapping.RunResult runPair(
            DetectorContext ctx,
            KgInput kg,
            KgInput.Version doc,
            List<KgInput.Sentence> sentences,
            Supplier<TextDto> annotation,
            String textKey,
            KgInput.Version model,
            ModelFormat format,
            List<String> approaches,
            List<String> warnings) {
        Map<String, Mapping.ModelEl> byLocalKey = new LinkedHashMap<>();
        Map<String, String> localKeyById = new HashMap<>();
        for (KgInput.ModelElement e : kg.modelElements(model.id())) {
            if (byLocalKey.put(e.localKey(), new Mapping.ModelEl(e.key(), e.name())) != null) {
                throw new IllegalStateException(
                        "model " + model.path() + " has two elements with local key " + e.localKey());
            }
            localKeyById.put(e.id(), e.localKey());
        }
        Map<String, Integer> indexById = new HashMap<>();
        for (int i = 0; i < sentences.size(); i++) {
            indexById.put(sentences.get(i).id(), i);
        }
        List<ExternalLink> links = new ArrayList<>();
        for (var pair : kg.links(doc.id(), model.id(), approaches)) {
            Integer sentence = indexById.get(pair.sentenceId());
            String key = localKeyById.get(pair.modelElementId());
            if (sentence != null && key != null) {
                links.add(new ExternalLink(sentence, key));
            }
        }
        String key = Ids.sha256Hex(
                String.join("\0", textKey, format.name(), model.storageUri(), fileName(model), links.toString()));
        Cached hit = CACHE.get(key);
        if (hit != null) {
            warnings.add(hit.summary(doc, model));
            return result(doc, model, sentences, byLocalKey, hit.raw());
        }
        acquire(ctx);
        Path dir = null;
        try {
            dir = Files.createTempDirectory("ardoco-id-");
            Path modelFile = dir.resolve(fileName(model));
            Files.copy(ctx.artifacts().pathFor(model.storageUri()), modelFile);
            RUNS.incrementAndGet();
            long start = System.nanoTime();
            List<MemoryPoolMXBean> heap = ManagementFactory.getMemoryPoolMXBeans().stream()
                    .filter(p -> p.getType() == MemoryType.HEAP)
                    .toList();
            heap.forEach(MemoryPoolMXBean::resetPeakUsage);
            Text text = CorenlpClient.convert(annotation.get());
            var stats = new ExternalLinksStep.Stats();
            var raw = IdRunner.extract(IdRunner.run(text, modelFile, format, links, stats));
            long peak = heap.stream().mapToLong(p -> p.getPeakUsage().getUsed()).sum();
            var cached = new Cached(stats.toString(), raw);
            CACHE.put(key, cached);
            String summary = cached.summary(doc, model);
            warnings.add(summary);
            LOG.info(
                    "ardoco-id run: {} ({} sentences), {} ms, peak heap {} MiB",
                    summary,
                    sentences.size(),
                    (System.nanoTime() - start) / 1_000_000,
                    peak >> 20);
            return result(doc, model, sentences, byLocalKey, raw);
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        } finally {
            IdRunner.LOCK.unlock();
            if (dir != null) {
                delete(dir);
            }
        }
    }

    private static String fileName(KgInput.Version model) {
        return Path.of(model.path()).getFileName().toString();
    }

    private static Mapping.RunResult result(
            KgInput.Version doc,
            KgInput.Version model,
            List<KgInput.Sentence> sentences,
            Map<String, Mapping.ModelEl> byLocalKey,
            Mapping.Raw raw) {
        return new Mapping.RunResult(
                doc.id(),
                model.id(),
                sentences.stream().map(s -> new Mapping.Sentence(s.key())).toList(),
                byLocalKey,
                raw);
    }

    /** Artifact format, else the file suffix (as the tlr approach does). */
    static ModelFormat format(KgInput.Version model) {
        if (model.format() != null) {
            return FORMATS.get(model.format());
        }
        String path = model.path().toLowerCase(Locale.ROOT);
        if (path.endsWith(".repository")) {
            return ModelFormat.PCM;
        }
        return path.endsWith(".uml") || path.endsWith(".xmi") ? ModelFormat.UML : null;
    }

    static List<String> strings(Object value, List<String> fallback) {
        if (value == null) {
            return fallback;
        }
        if (value instanceof List<?> list) {
            return list.stream().map(String::valueOf).toList();
        }
        return List.of(String.valueOf(value));
    }

    static void cancelCheck(DetectorContext ctx) {
        if (ctx.cancelled().getAsBoolean()) {
            throw new CancellationException("cancelled");
        }
    }

    static void acquire(DetectorContext ctx) {
        try {
            while (!IdRunner.LOCK.tryLock(100, TimeUnit.MILLISECONDS)) {
                cancelCheck(ctx);
            }
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new CancellationException("interrupted");
        }
    }

    static void delete(Path dir) {
        try (Stream<Path> files = Files.walk(dir)) {
            files.sorted(Comparator.reverseOrder()).forEach(p -> {
                if (!p.toFile().delete()) {
                    LOG.warn("ardoco-id: could not delete {}", p);
                }
            });
        } catch (IOException e) {
            LOG.warn("ardoco-id: could not clean up temp dir {}", dir, e);
        }
    }
}
