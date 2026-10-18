package edu.kit.kastel.mcse.ardoco.sandman.tlr.ardoco;

import com.fasterxml.jackson.datatype.jdk8.Jdk8Module;
import edu.kit.kastel.mcse.ardoco.core.api.PreprocessingData;
import edu.kit.kastel.mcse.ardoco.core.api.models.ArchitectureModel;
import edu.kit.kastel.mcse.ardoco.core.api.models.CodeModel;
import edu.kit.kastel.mcse.ardoco.core.api.models.architecture.ArchitectureItem;
import edu.kit.kastel.mcse.ardoco.core.api.models.code.CodeItem;
import edu.kit.kastel.mcse.ardoco.core.api.models.CodeModelWithCompilationUnits;
import edu.kit.kastel.mcse.ardoco.core.api.models.Metamodel;
import edu.kit.kastel.mcse.ardoco.core.api.models.ModelFormat;
import edu.kit.kastel.mcse.ardoco.core.api.models.ModelStates;
import edu.kit.kastel.mcse.ardoco.core.api.models.code.CodeCompilationUnit;
import edu.kit.kastel.mcse.ardoco.core.api.output.ArdocoResult;
import edu.kit.kastel.mcse.ardoco.core.api.stage.codetraceability.CodeTraceabilityState;
import edu.kit.kastel.mcse.ardoco.core.api.stage.codetraceability.ArchitectureCodeTraceLink;
import edu.kit.kastel.mcse.ardoco.core.data.DataRepository;
import edu.kit.kastel.mcse.ardoco.core.pipeline.AbstractExecutionStage;
import edu.kit.kastel.mcse.ardoco.core.pipeline.agent.Informant;
import edu.kit.kastel.mcse.ardoco.core.pipeline.agent.PipelineAgent;
import edu.kit.kastel.mcse.ardoco.core.api.text.Text;
import edu.kit.kastel.mcse.ardoco.core.common.IdentifierProvider;
import edu.kit.kastel.mcse.ardoco.core.common.JsonHandling;
import edu.kit.kastel.mcse.ardoco.core.common.util.CommonUtilities;
import edu.kit.kastel.mcse.ardoco.core.common.util.DataRepositoryHelper;
import edu.kit.kastel.mcse.ardoco.core.execution.Ardoco;
import edu.kit.kastel.mcse.ardoco.core.execution.runner.ArdocoRunner;
import edu.kit.kastel.mcse.ardoco.sandman.agent.Ids;
import edu.kit.kastel.mcse.ardoco.sandman.ardocotext.CorenlpClient;
import edu.kit.kastel.mcse.ardoco.sandman.ardocotext.CorenlpUnavailableException;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.ApproachUnavailableException;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.CandidateLink;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.KindPair;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.ScopeMode;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.TlrContext;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.TlrInput;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.TraceLinkApproach;
import edu.kit.kastel.mcse.ardoco.tlr.codetraceability.CodeTraceabilityStateImpl;
import edu.kit.kastel.mcse.ardoco.tlr.codetraceability.SadSamCodeTraceabilityLinkRecovery;
import edu.kit.kastel.mcse.ardoco.tlr.codetraceability.informants.arcotl.TraceLinkGenerator;
import edu.kit.kastel.mcse.ardoco.tlr.connectiongenerator.ConnectionGenerator;
import edu.kit.kastel.mcse.ardoco.tlr.models.agents.ArchitectureConfiguration;
import edu.kit.kastel.mcse.ardoco.tlr.models.agents.ModelProviderAgent;
import edu.kit.kastel.mcse.ardoco.tlr.recommendationgenerator.RecommendationGenerator;
import edu.kit.kastel.mcse.ardoco.tlr.text.providers.TextPreprocessingAgent;
import edu.kit.kastel.mcse.ardoco.tlr.textextraction.TextExtraction;
import java.io.File;
import java.io.IOException;
import java.io.UncheckedIOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Collection;
import java.util.Collections;
import java.util.HashMap;
import java.util.LinkedHashSet;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeSet;
import java.util.concurrent.CancellationException;
import org.eclipse.collections.api.factory.SortedMaps;
import org.eclipse.collections.api.map.sorted.ImmutableSortedMap;

/**
 * ArDoCo TransArC (SAD to SAM to code, T2.7): each current architecture document against each current architecture
 * model and the ACM of the current code. Writes SAD to code links (pair doc to code, as {@link ArdocodeApproach}) and
 * SAM to code links (pair model to code); SAD to SAM links stay with {@link ArdocoApproach}.
 */
public final class TransarcApproach implements TraceLinkApproach {

    public static final String ID = "transarc";
    /** Raise the +aN suffix when the mapping of ArDoCo results to links changes. */
    public static final String VERSION = "2.1.0+a1";

    /**
     * SAM to code links as (architecture item id, code item id) by approach version, model file hash and ACM hash.
     * ArCoTL reads only the two models, so every document of a model shares them. Bounded LRU per JVM.
     */
    private static final Map<String, List<Map.Entry<String, String>>> SAM_CODE =
            Collections.synchronizedMap(new LinkedHashMap<>(16, 0.75f, true) {
                @Override
                protected boolean removeEldestEntry(Map.Entry<String, List<Map.Entry<String, String>>> eldest) {
                    return size() > 256;
                }
            });

    private final CorenlpClient corenlp;
    private final CodeModelClient codeModel;

    static void clearCache() {
        SAM_CODE.clear();
    }

    /** ServiceLoader entry: settings from the environment, read when the first run needs them. */
    public TransarcApproach() {
        this(null, null);
    }

    TransarcApproach(CorenlpClient corenlp, CodeModelClient codeModel) {
        this.corenlp = corenlp;
        this.codeModel = codeModel;
    }

    private CorenlpClient corenlp() {
        return corenlp != null ? corenlp : CorenlpClient.fromEnv();
    }

    private CodeModelClient codeModel() {
        return codeModel != null ? codeModel : CodeModelClient.fromEnv();
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
        return Set.of(new KindPair("architecture-doc", "code"), new KindPair("architecture-model", "code"));
    }

    @Override
    public ScopeMode scopeMode() {
        return ScopeMode.WHOLE_PROJECT;
    }

    @Override
    public Set<String> paramKeys() {
        return Set.of();
    }

    @Override
    public void callFinished() {
        TextCache.clear();
    }

    @Override
    public String environment(TlrContext ctx) {
        try {
            String provider = corenlp().provider(ctx.cancelled()).identity();
            try (CodeModelClient client = codeModel()) {
                return provider + ";codeModel=" + client.identity();
            }
        } catch (CorenlpUnavailableException e) {
            throw new ApproachUnavailableException(e.getMessage(), e);
        }
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
        List<TlrInput.Version> docs = versions(in, "architecture-doc", TlrInput.Version::id);
        List<TlrInput.Version> models = versions(in, "architecture-model", TlrInput.Version::id);
        List<TlrInput.Version> code = versions(in, "code", TlrInput.Version::path);
        Map<String, CandidateLink> links = new LinkedHashMap<>();
        if (docs.isEmpty() || models.isEmpty() || code.isEmpty()) {
            return new ArrayList<>(links.values());
        }
        Map<String, TlrInput.Element> files = new LinkedHashMap<>();
        for (TlrInput.Element t : in.targets()) {
            if ("file".equals(t.kind()) && "CodeUnit".equals(t.label()) && files.put(t.localKey(), t) != null) {
                throw new IllegalStateException("two file elements with local key " + t.localKey());
            }
        }
        if (ctx.artifacts() == null) {
            throw new IllegalStateException("no artifact store configured, cannot read the code");
        }
        CorenlpClient annotator = corenlp();
        CorenlpClient.Provider provider = null;
        Path dir = null;
        try {
            dir = Files.createTempDirectory("transarc-");
            ArdocodeApproach.Acm acm = null;
            for (TlrInput.Version doc : docs) {
                List<TlrInput.Element> sentences = SentenceRendering.sentences(in.sources(), doc.id());
                if (sentences.isEmpty()) {
                    ctx.log().warn("transarc: document version {} has no sentences, skipped", doc.id());
                    continue;
                }
                Text annotated = null;
                for (TlrInput.Version model : models) {
                    if (!model.pairsWithDoc(doc.id())) {
                        continue;
                    }
                    ModelFormat format = ArdocoApproach.format(model);
                    if (format == null) {
                        ctx.log()
                                .warn(
                                        "transarc: model {} has unsupported format {}, skipped",
                                        model.path(),
                                        model.format());
                        continue;
                    }
                    if (ctx.cancelled().getAsBoolean()) {
                        throw new CancellationException("cancelled");
                    }
                    if (acm == null) {
                        acm = ArdocodeApproach.fetchAcm(codeModel, in, code, ctx, dir);
                    }
                    if (annotated == null) {
                        SentenceRendering.assertHashes(sentences);
                        if (provider == null) {
                            provider = annotator.provider(ctx.cancelled());
                            ctx.log().info("transarc: CoreNLP {}", provider.identity());
                        }
                        ctx.progress().accept("transarc: annotating " + sentences.size() + " sentences with CoreNLP");
                        annotated = TextCache.annotate(annotator, provider, SentenceRendering.lines(sentences), ctx);
                    }
                    ctx.progress().accept("transarc: running ArDoCo on " + model.path());
                    run(in, ctx, dir, acm, sentences, annotated, model, format, files, links);
                }
            }
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        } finally {
            if (dir != null) {
                ArdocoRuns.delete(dir, ctx);
            }
        }
        return new ArrayList<>(links.values());
    }

    private static List<TlrInput.Version> versions(
            TlrInput in, String kind, java.util.function.Function<TlrInput.Version, String> order) {
        return in.versions().stream()
                .filter(v -> kind.equals(v.kind()))
                .sorted(Comparator.comparing(order))
                .toList();
    }

    private void run(
            TlrInput in,
            TlrContext ctx,
            Path dir,
            ArdocodeApproach.Acm acm,
            List<TlrInput.Element> sentences,
            Text annotated,
            TlrInput.Version model,
            ModelFormat format,
            Map<String, TlrInput.Element> files,
            Map<String, CandidateLink> links)
            throws IOException {
        Map<String, TlrInput.Element> modelElements = new LinkedHashMap<>();
        for (TlrInput.Element e : in.sources()) {
            if (model.id().equals(e.versionId()) && modelElements.put(e.localKey(), e) != null) {
                throw new IllegalStateException(
                        "model " + model.path() + " has two elements with local key " + e.localKey());
            }
        }
        Path run = Files.createTempDirectory(dir, "run-");
        Path modelFile = ArdocoRuns.stageModel(ctx, run, model);
        Path textFile = run.resolve("text.txt");
        Files.writeString(textFile, SentenceRendering.text(SentenceRendering.lines(sentences)), StandardCharsets.UTF_8);
        ArdocoRuns.acquire(ctx);
        try {
            long start = System.nanoTime();
            ArdocoRuns.resetHeapPeak();
            var runner = new TransarcRunner();
            String samKey = Ids.sha256Hex(String.join(
                    "\0", VERSION, format.name(), Ids.sha256Hex(Files.readAllBytes(modelFile)), acm.sha()));
            runner.setUp(
                    textFile.toFile(),
                    new ArchitectureConfiguration(modelFile.toFile(), format),
                    acm.file().toFile(),
                    samKey);
            DataRepositoryHelper.putPreprocessingData(
                    runner.getArdoco().getDataRepository(), new PreprocessingData(annotated));
            var repository = runner.runWithoutSaving();
            if (repository == null) {
                throw new IllegalStateException("ArDoCo run did not start");
            }
            var result = new ArdocoResult(repository);
            List<Map.Entry<Integer, String>> sadCode = new ArrayList<>();
            for (var tl : result.getSadCodeTraceLinks()) {
                sadCode.add(Map.entry(
                        tl.getFirstEndpoint().getSentence().getSentenceNumber(),
                        ArdocodeApproach.pathOf((CodeCompilationUnit) tl.getSecondEndpoint())));
            }
            List<Map.Entry<String, String>> samCode = new ArrayList<>();
            for (var tl : result.getSamCodeTraceLinks()) {
                samCode.add(Map.entry(tl.getFirstEndpoint().getId(), ArdocodeApproach.pathOf((CodeCompilationUnit)
                        tl.getSecondEndpoint())));
            }
            ArdocodeApproach.toLinks(sadCode, sentences, files).forEach(l -> links.put(key(l), l));
            samToLinks(samCode, modelElements, files, model).forEach(l -> links.put(key(l), l));
            ctx.log()
                    .info(
                            "transarc run: model {} ({} sentences), {} SAD-code and {} SAM-code pairs, {} ms, peak heap {} MiB",
                            model.path(),
                            sentences.size(),
                            sadCode.size(),
                            samCode.size(),
                            (System.nanoTime() - start) / 1_000_000,
                            ArdocoRuns.peakHeapMiB());
        } finally {
            ArdocoRuns.LOCK.unlock();
        }
    }

    private static String key(CandidateLink l) {
        return l.sourceId() + ">" + l.targetId();
    }

    /** One link per distinct (model id, file); a model id or path that is not known fails the run. */
    static List<CandidateLink> samToLinks(
            Collection<Map.Entry<String, String>> pairs,
            Map<String, TlrInput.Element> modelElements,
            Map<String, TlrInput.Element> files,
            TlrInput.Version model) {
        Set<Map.Entry<String, String>> distinct =
                new TreeSet<>(Map.Entry.<String, String>comparingByKey().thenComparing(Map.Entry.comparingByValue()));
        distinct.addAll(pairs);
        List<CandidateLink> links = new ArrayList<>();
        for (var pair : distinct) {
            TlrInput.Element source = modelElements.get(pair.getKey());
            TlrInput.Element target = files.get(pair.getValue());
            if (source == null || target == null) {
                throw new IllegalStateException("ArDoCo link between unknown model id " + pair.getKey() + " (model "
                        + model.path() + ") or unknown file " + pair.getValue());
            }
            links.add(new CandidateLink(source.id(), target.id(), "implements", 1.0, null));
        }
        return links;
    }

    /**
     * ArDoCo's {@code Transarc} runner with the ACM read as in {@code ArdocodeApproach.SadCodeRunner}: the real runner
     * reads it with {@code CodeExtractor.readInCodeModel}, whose class initializer needs Magika (not on this module's
     * class path). The benchmark test compares it with the real runner.
     */
    // ponytail: copy of Transarc.definePipeline for ArDoCo 2.1.0; an ArDoCo update can change the real pipeline without
    // a compile error here. Guard: VersionTest and the benchmark equivalence test. Upgrade path: use Transarc itself
    // once it loads without Magika.
    private static final class TransarcRunner extends ArdocoRunner {

        TransarcRunner() {
            super("sandman");
        }

        void setUp(File textFile, ArchitectureConfiguration architecture, File acm, String samKey) throws IOException {
            Ardoco ardoco = getArdoco();
            var repository = ardoco.getDataRepository();
            String text = CommonUtilities.readInputText(textFile);
            if (text.isBlank()) {
                throw new IllegalArgumentException("Cannot deal with empty input text");
            }
            DataRepositoryHelper.putInputText(repository, text);
            var configs = SortedMaps.immutable.<String, String>empty();
            ardoco.addPipelineStep(TextPreprocessingAgent.get(configs, repository));
            var mapper = JsonHandling.createObjectMapper();
            mapper.registerModule(new Jdk8Module());
            CodeModel model = new CodeModelWithCompilationUnits(mapper.readValue(acm, CodeModel.CodeModelDto.class));
            var states = new ModelStates();
            states.addModel(Metamodel.CODE_WITH_COMPILATION_UNITS, model);
            repository.addData(ModelStates.ID, states);
            IdentifierProvider.reset();
            ardoco.addPipelineStep(ModelProviderAgent.getModelProviderAgent(
                    repository, configs, architecture.withMetamodel(Metamodel.ARCHITECTURE_WITH_COMPONENTS), null));
            ardoco.addPipelineStep(TextExtraction.get(configs, repository));
            ardoco.addPipelineStep(RecommendationGenerator.get(configs, repository));
            ardoco.addPipelineStep(ConnectionGenerator.get(configs, repository));
            ardoco.addPipelineStep(SamCodeStage.get(configs, repository, samKey));
            ardoco.addPipelineStep(SadSamCodeTraceabilityLinkRecovery.get(configs, repository));
            isSetUp = true;
        }
    }

    /** {@code SamCodeTraceabilityLinkRecovery} whose informant reuses the links of {@link #SAM_CODE}. */
    private static final class SamCodeStage extends AbstractExecutionStage {

        private SamCodeStage(DataRepository repository, String key) {
            super(
                    List.of(new PipelineAgent(
                            List.of(new CachedArCoTL(repository, key)), "InitialCodeTraceabilityAgent", repository) {
                        @Override
                        protected void initializeState() {}
                    }),
                    "SamCodeTraceabilityLinkRecovery",
                    repository);
        }

        static SamCodeStage get(ImmutableSortedMap<String, String> configs, DataRepository repository, String key) {
            var stage = new SamCodeStage(repository, key);
            stage.applyConfiguration(configs);
            return stage;
        }

        @Override
        protected void initializeState() {
            DataRepository repository = getDataRepository();
            if (!DataRepositoryHelper.hasCodeTraceabilityState(repository)) {
                repository.addData(CodeTraceabilityState.ID, new CodeTraceabilityStateImpl());
            }
        }
    }

    // ponytail: copy of ArCoTLInformant.process for ArDoCo 2.1.0 plus the SAM_CODE lookup (guard: TransarcEquivalenceTest).
    private static final class CachedArCoTL extends Informant {

        private final String key;

        CachedArCoTL(DataRepository repository, String key) {
            super("ArCoTLInformant", repository);
            this.key = key;
        }

        @Override
        public void process() {
            var repository = getDataRepository();
            var modelStates = DataRepositoryHelper.getModelStatesData(repository);
            ArchitectureModel arch = null;
            CodeModel code = null;
            for (var metamodel : modelStates.getMetamodels()) {
                var model = modelStates.getModel(metamodel);
                if (model instanceof ArchitectureModel a) {
                    arch = a;
                } else if (model instanceof CodeModel c) {
                    code = c;
                }
            }
            List<Map.Entry<String, String>> pairs = SAM_CODE.get(key);
            Set<ArchitectureCodeTraceLink> links = pairs == null ? null : rebuild(pairs, arch, code);
            if (links == null) {
                links = TraceLinkGenerator.generateTraceLinks(TraceLinkGenerator.getRoot(), arch, code);
                List<Map.Entry<String, String>> ids = new ArrayList<>();
                for (var l : links) {
                    ids.add(Map.entry(
                            l.getFirstEndpoint().getId(), l.getSecondEndpoint().getId()));
                }
                SAM_CODE.put(key, ids);
            }
            DataRepositoryHelper.getCodeTraceabilityState(repository).addSamCodeTraceLinks(links);
        }

        /** Null if an id is unknown in the models, so the caller computes the links. */
        private static Set<ArchitectureCodeTraceLink> rebuild(
                List<Map.Entry<String, String>> pairs, ArchitectureModel arch, CodeModel code) {
            Map<String, ArchitectureItem> archById = new HashMap<>();
            arch.getEndpoints().forEach(e -> archById.put(e.getId(), e));
            Map<String, CodeItem> codeById = new HashMap<>();
            code.getEndpoints().forEach(e -> codeById.put(e.getId(), e));
            Set<ArchitectureCodeTraceLink> links = new LinkedHashSet<>();
            for (var pair : pairs) {
                var a = archById.get(pair.getKey());
                var c = codeById.get(pair.getValue());
                if (a == null || c == null) {
                    return null;
                }
                links.add(new ArchitectureCodeTraceLink(a, c));
            }
            return links;
        }

        @Override
        protected void delegateApplyConfigurationToInternalObjects(ImmutableSortedMap<String, String> configuration) {}
    }
}
