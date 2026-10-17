package edu.kit.kastel.mcse.ardoco.sandman.tlr.ardoco;

import edu.kit.kastel.mcse.ardoco.core.api.PreprocessingData;
import edu.kit.kastel.mcse.ardoco.core.api.models.ModelFormat;
import edu.kit.kastel.mcse.ardoco.core.api.output.ArdocoResult;
import edu.kit.kastel.mcse.ardoco.core.api.text.Text;
import edu.kit.kastel.mcse.ardoco.core.common.util.DataRepositoryHelper;
import edu.kit.kastel.mcse.ardoco.sandman.ardocotext.CorenlpClient;
import edu.kit.kastel.mcse.ardoco.sandman.ardocotext.CorenlpUnavailableException;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.ApproachUnavailableException;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.CandidateLink;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.KindPair;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.ScopeMode;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.TlrContext;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.TlrInput;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.TraceLinkApproach;
import edu.kit.kastel.mcse.ardoco.tlr.execution.Swattr;
import edu.kit.kastel.mcse.ardoco.tlr.models.agents.ArchitectureConfiguration;
import java.io.File;
import java.io.IOException;
import java.io.UncheckedIOException;
import java.lang.management.ManagementFactory;
import java.lang.management.MemoryPoolMXBean;
import java.lang.management.MemoryType;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeSet;
import java.util.concurrent.CancellationException;
import org.eclipse.collections.api.factory.SortedMaps;

/**
 * ArDoCo SWATTR (SAD to SAM) over all current architecture documents and models (T2.1). The sentences come from the KG,
 * one per line. The annotation comes from the CoreNLP provider service through the corenlp-gateway (T2.5), checked so
 * that ArDoCo sentence n is rendered line n.
 */
public final class ArdocoApproach implements TraceLinkApproach {

    public static final String ID = "ardoco";
    /** Raise the +aN suffix when the mapping of ArDoCo results to links changes. */
    public static final String VERSION = "2.1.0+a2";

    private static final Map<String, ModelFormat> FORMATS =
            Map.of("pcm-repository", ModelFormat.PCM, "uml", ModelFormat.UML);
    private final CorenlpClient client;

    /** ServiceLoader entry: settings from the environment, read when the first run needs them. */
    public ArdocoApproach() {
        this(null);
    }

    ArdocoApproach(CorenlpClient client) {
        this.client = client;
    }

    private CorenlpClient client() {
        return client != null ? client : CorenlpClient.fromEnv();
    }

    @Override
    public String id() {
        return ID;
    }

    @Override
    public String version() {
        return VERSION;
    }

    @Override
    public Set<KindPair> supports() {
        return Set.of(new KindPair("architecture-doc", "architecture-model"));
    }

    @Override
    public ScopeMode scopeMode() {
        return ScopeMode.WHOLE_PROJECT;
    }

    /** ArDoCo additional configs are not passed through: no parameter is needed, so none is accepted. */
    @Override
    public Set<String> paramKeys() {
        return Set.of();
    }

    /** The provider's /version answer and the eolonly mode enter configHash: other providers, other link ids. A provider that is down throws ApproachUnavailableException. */
    @Override
    public void callFinished() {
        TextCache.clear();
    }

    @Override
    public String environment(TlrContext ctx) {
        try {
            return environmentUnwrapped(ctx);
        } catch (CorenlpUnavailableException e) {
            throw new ApproachUnavailableException(e.getMessage(), e);
        }
    }

    private String environmentUnwrapped(TlrContext ctx) {
        return client().provider(ctx.cancelled()).identity();
    }

    @Override
    public List<CandidateLink> recover(TlrInput in, TlrContext ctx) {
        try {
            return recoverUnwrapped(in, ctx);
        } catch (CorenlpUnavailableException e) {
            throw new ApproachUnavailableException(e.getMessage(), e);
        }
    }

    private List<CandidateLink> recoverUnwrapped(TlrInput in, TlrContext ctx) {
        List<TlrInput.Version> docs = versions(in, "architecture-doc");
        List<TlrInput.Version> models = versions(in, "architecture-model");
        Map<String, CandidateLink> links = new LinkedHashMap<>();
        CorenlpClient corenlp = client();
        CorenlpClient.Provider provider = null;
        for (TlrInput.Version doc : docs) {
            List<TlrInput.Element> sentences = SentenceRendering.sentences(in.sources(), doc.id());
            if (sentences.isEmpty()) {
                ctx.log().warn("ardoco: document version {} has no sentences, skipped", doc.id());
                continue;
            }
            Text annotated = null;
            for (TlrInput.Version model : models) {
                ModelFormat format = format(model);
                if (format == null) {
                    ctx.log().warn("ardoco: model {} has unsupported format {}, skipped", model.path(), model.format());
                    continue;
                }
                if (ctx.cancelled().getAsBoolean()) {
                    throw new CancellationException("cancelled");
                }
                Map<String, TlrInput.Element> modelElements = new LinkedHashMap<>();
                for (TlrInput.Element t : in.targets()) {
                    if (model.id().equals(t.versionId()) && modelElements.put(t.localKey(), t) != null) {
                        throw new IllegalStateException(
                                "model " + model.path() + " has two elements with local key " + t.localKey());
                    }
                }
                if (annotated == null) {
                    SentenceRendering.assertHashes(sentences);
                    if (provider == null) {
                        provider = corenlp.provider(ctx.cancelled());
                        ctx.log().info("ardoco: CoreNLP {}", provider.identity());
                    }
                    ctx.progress().accept("ardoco: annotating " + sentences.size() + " sentences with CoreNLP");
                    annotated = TextCache.annotate(corenlp, provider, SentenceRendering.lines(sentences), ctx);
                }
                ctx.progress().accept("ardoco: running ArDoCo on " + model.path());
                runPair(in, ctx, sentences, annotated, modelElements, model, format)
                        .forEach(l -> links.put(l.sourceId() + ">" + l.targetId(), l));
            }
        }
        return new ArrayList<>(links.values());
    }

    /** Artifact format, else the file suffix like the extraction-agent's built-in dispatch (kind rules need no format). */
    static ModelFormat format(TlrInput.Version model) {
        if (model.format() != null) {
            return FORMATS.get(model.format());
        }
        String path = model.path().toLowerCase(java.util.Locale.ROOT);
        if (path.endsWith(".repository")) {
            return ModelFormat.PCM;
        }
        return path.endsWith(".uml") || path.endsWith(".xmi") ? ModelFormat.UML : null;
    }

    private static List<TlrInput.Version> versions(TlrInput in, String kind) {
        return in.versions().stream()
                .filter(v -> kind.equals(v.kind()))
                .sorted(Comparator.comparing(TlrInput.Version::id))
                .toList();
    }

    private List<CandidateLink> runPair(
            TlrInput in,
            TlrContext ctx,
            List<TlrInput.Element> sentences,
            Text annotated,
            Map<String, TlrInput.Element> modelElements,
            TlrInput.Version model,
            ModelFormat format) {
        List<String> lines = SentenceRendering.lines(sentences);
        if (ctx.artifacts() == null) {
            throw new IllegalStateException("no artifact store configured, cannot read " + model.path());
        }
        Path dir = null;
        ArdocoRuns.acquire(ctx);
        try {
            dir = Files.createTempDirectory("ardoco-");
            Path modelFile = dir.resolve(Path.of(model.path()).getFileName().toString());
            Files.copy(ctx.artifacts().pathFor(model.storageUri()), modelFile);
            Path textFile = dir.resolve("text.txt");
            String text = SentenceRendering.text(lines);
            Files.writeString(textFile, text, StandardCharsets.UTF_8);

            long start = System.nanoTime();
            List<MemoryPoolMXBean> heap = ManagementFactory.getMemoryPoolMXBeans().stream()
                    .filter(p -> p.getType() == MemoryType.HEAP)
                    .toList();
            heap.forEach(MemoryPoolMXBean::resetPeakUsage);

            var runner = new Swattr("sandman");
            runner.setUp(
                    textFile.toFile(),
                    new ArchitectureConfiguration(new File(modelFile.toString()), format),
                    SortedMaps.immutable.empty(),
                    dir.resolve("out").toFile());
            DataRepositoryHelper.putPreprocessingData(
                    runner.getArdoco().getDataRepository(), new PreprocessingData(annotated));
            var repository = runner.runWithoutSaving();
            if (repository == null) {
                throw new IllegalStateException("ArDoCo run did not start");
            }
            var result = new ArdocoResult(repository);

            Set<Map.Entry<Integer, String>> pairs = new TreeSet<>(
                    Map.Entry.<Integer, String>comparingByKey().thenComparing(Map.Entry.comparingByValue()));
            result.getArchitectureTraceLinks()
                    .forEach(tl -> pairs.add(Map.entry(
                            tl.getFirstEndpoint().getSentence().getSentenceNumber(),
                            tl.getSecondEndpoint().getId())));
            List<CandidateLink> links = new ArrayList<>();
            for (var pair : pairs) {
                TlrInput.Element target = modelElements.get(pair.getValue());
                if (target == null || pair.getKey() < 0 || pair.getKey() >= sentences.size()) {
                    throw new IllegalStateException("ArDoCo link to unknown model id " + pair.getValue()
                            + " or sentence " + pair.getKey() + " (model " + model.path() + ")");
                }
                links.add(new CandidateLink(sentences.get(pair.getKey()).id(), target.id(), "documents", 1.0, null));
            }
            long peak = heap.stream().mapToLong(p -> p.getPeakUsage().getUsed()).sum();
            ctx.log()
                    .info(
                            "ardoco run: model {} ({} sentences), {} links, {} ms, peak heap {} MiB",
                            model.path(),
                            lines.size(),
                            links.size(),
                            (System.nanoTime() - start) / 1_000_000,
                            peak >> 20);
            return links;
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        } finally {
            ArdocoRuns.LOCK.unlock();
            if (dir != null) {
                ArdocoRuns.delete(dir, ctx);
            }
        }
    }
}
