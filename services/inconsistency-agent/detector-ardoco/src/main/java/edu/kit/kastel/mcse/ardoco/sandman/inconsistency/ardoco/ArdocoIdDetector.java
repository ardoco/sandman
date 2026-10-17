package edu.kit.kastel.mcse.ardoco.sandman.inconsistency.ardoco;

import edu.kit.kastel.mcse.ardoco.core.api.models.ModelFormat;
import edu.kit.kastel.mcse.ardoco.core.api.text.Text;
import edu.kit.kastel.mcse.ardoco.core.textproviderjson.dto.TextDto;
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
import java.util.Comparator;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.CancellationException;
import java.util.concurrent.TimeUnit;
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
            TextDto annotated = null;
            for (KgInput.Version model : models) {
                ModelFormat format = format(model);
                if (format == null) {
                    warnings.add("model " + model.path() + " has unsupported format " + model.format() + ", skipped");
                    continue;
                }
                cancelCheck(ctx);
                if (annotated == null) {
                    for (int i = 0; i < sentences.size(); i++) {
                        var s = sentences.get(i);
                        SentenceRendering.assertHash(i, doc.id(), s.localKey(), s.text());
                    }
                    if (provider == null) {
                        provider = corenlp.provider(ctx.cancelled());
                        LOG.info("ardoco-id: CoreNLP {}", provider.identity());
                    }
                    List<String> lines = sentences.stream()
                            .map(s -> SentenceRendering.line(s.text()))
                            .toList();
                    annotated = corenlp.annotateDto(lines, provider, ctx.cancelled());
                }
                results.add(runPair(ctx, kg, doc, sentences, annotated, model, format, approaches, warnings));
            }
        }
        return new Detection(Mapping.map(results), warnings);
    }

    private static Mapping.RunResult runPair(
            DetectorContext ctx,
            KgInput kg,
            KgInput.Version doc,
            List<KgInput.Sentence> sentences,
            TextDto annotated,
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
        acquire(ctx);
        Path dir = null;
        try {
            dir = Files.createTempDirectory("ardoco-id-");
            Path modelFile = dir.resolve(Path.of(model.path()).getFileName().toString());
            Files.copy(ctx.artifacts().pathFor(model.storageUri()), modelFile);
            long start = System.nanoTime();
            List<MemoryPoolMXBean> heap = ManagementFactory.getMemoryPoolMXBeans().stream()
                    .filter(p -> p.getType() == MemoryType.HEAP)
                    .toList();
            heap.forEach(MemoryPoolMXBean::resetPeakUsage);
            Text text = CorenlpClient.convert(annotated);
            var stats = new ExternalLinksStep.Stats();
            var raw = IdRunner.extract(IdRunner.run(text, modelFile, format, links, stats));
            long peak = heap.stream().mapToLong(p -> p.getPeakUsage().getUsed()).sum();
            String summary = "doc " + doc.path() + " x model " + model.path() + ": " + stats + ", "
                    + raw.models().size() + " model and " + raw.words().size() + " text findings";
            warnings.add(summary);
            LOG.info(
                    "ardoco-id run: {} ({} sentences), {} ms, peak heap {} MiB",
                    summary,
                    sentences.size(),
                    (System.nanoTime() - start) / 1_000_000,
                    peak >> 20);
            return new Mapping.RunResult(
                    doc.id(),
                    model.id(),
                    sentences.stream().map(s -> new Mapping.Sentence(s.key())).toList(),
                    byLocalKey,
                    raw);
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        } finally {
            IdRunner.LOCK.unlock();
            if (dir != null) {
                delete(dir);
            }
        }
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

    private static List<String> strings(Object value, List<String> fallback) {
        if (value == null) {
            return fallback;
        }
        if (value instanceof List<?> list) {
            return list.stream().map(String::valueOf).toList();
        }
        return List.of(String.valueOf(value));
    }

    private static void cancelCheck(DetectorContext ctx) {
        if (ctx.cancelled().getAsBoolean()) {
            throw new CancellationException("cancelled");
        }
    }

    private static void acquire(DetectorContext ctx) {
        try {
            while (!IdRunner.LOCK.tryLock(100, TimeUnit.MILLISECONDS)) {
                cancelCheck(ctx);
            }
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new CancellationException("interrupted");
        }
    }

    private static void delete(Path dir) {
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
