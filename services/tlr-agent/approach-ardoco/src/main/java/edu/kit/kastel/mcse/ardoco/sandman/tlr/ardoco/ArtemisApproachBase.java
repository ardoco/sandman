package edu.kit.kastel.mcse.ardoco.sandman.tlr.ardoco;

import com.fasterxml.jackson.datatype.jdk8.Jdk8Module;
import edu.kit.kastel.mcse.ardoco.core.api.models.CodeModel;
import edu.kit.kastel.mcse.ardoco.core.api.models.CodeModelWithCompilationUnits;
import edu.kit.kastel.mcse.ardoco.core.api.models.Metamodel;
import edu.kit.kastel.mcse.ardoco.core.api.models.ModelFormat;
import edu.kit.kastel.mcse.ardoco.core.api.models.ModelStates;
import edu.kit.kastel.mcse.ardoco.core.api.models.code.ClassUnit;
import edu.kit.kastel.mcse.ardoco.core.api.models.code.CodeCompilationUnit;
import edu.kit.kastel.mcse.ardoco.core.api.models.code.CodeFile;
import edu.kit.kastel.mcse.ardoco.core.api.output.ArdocoResult;
import edu.kit.kastel.mcse.ardoco.core.common.IdentifierProvider;
import edu.kit.kastel.mcse.ardoco.core.common.JsonHandling;
import edu.kit.kastel.mcse.ardoco.core.common.util.DataRepositoryHelper;
import edu.kit.kastel.mcse.ardoco.core.data.DataRepository;
import edu.kit.kastel.mcse.ardoco.core.execution.Ardoco;
import edu.kit.kastel.mcse.ardoco.sandman.agent.LlmUnavailableException;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.ApproachUnavailableException;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.CandidateLink;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.KindPair;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.ScopeMode;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.TlrContext;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.TlrInput;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.TraceLinkApproach;
import edu.kit.kastel.mcse.ardoco.tlr.artemis.strategies.ArtemisNerStrategy;
import edu.kit.kastel.mcse.ardoco.tlr.artemis.strategies.ClassArtemisNerStrategy;
import edu.kit.kastel.mcse.ardoco.tlr.artemis.strategies.ComponentArtemisNerStrategy;
import edu.kit.kastel.mcse.ardoco.tlr.artemis.strategies.DatafileArtemisNerStrategy;
import edu.kit.kastel.mcse.ardoco.tlr.codetraceability.SadSamCodeTraceabilityLinkRecovery;
import edu.kit.kastel.mcse.ardoco.tlr.codetraceability.SamCodeTraceabilityLinkRecovery;
import edu.kit.kastel.mcse.ardoco.tlr.models.agents.ArchitectureConfiguration;
import edu.kit.kastel.mcse.ardoco.tlr.models.agents.ModelProviderAgent;
import edu.kit.kastel.mcse.ardoco.tlr.text.providers.SimpleTextPreprocessingAgent;
import java.io.IOException;
import java.io.UncheckedIOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.CancellationException;
import java.util.stream.Stream;
import tools.jackson.databind.json.JsonMapper;

/**
 * The ArTEMiS approaches (T3.8): NER of named entities in the sentences of an architecture document by the LLM, then
 * matching against components, classes or code files. Pipelines follow the runners {@code Artemis},
 * {@code ArtemisInTransArC} and {@code ArtemisInExArch} of ArDoCo, with the LLM roles on the gateway
 * ({@link ArtemisGateway}). The SWATTR stage is not part of any pipeline here, so the ArTEMiS links reach
 * {@code TraceLinkCombiner} in the TransArC and ExArch flavors.
 */
abstract class ArtemisApproachBase implements TraceLinkApproach {

    /** Raise the +aN suffix when the mapping of ArDoCo results to links changes. */
    static final String VERSION = "2.2.0-SNAPSHOT+21ef682.a1";

    enum Flavor {
        COMPONENT("component"),
        CLASS("class"),
        DATAFILE("datafile"),
        TRANSARC("component"),
        EXARCH("component");

        final String target;

        Flavor(String target) {
            this.target = target;
        }
    }

    private final String id;
    private final Flavor flavor;
    private final CodeModelClient codeModel;

    ArtemisApproachBase(String id, Flavor flavor, CodeModelClient codeModel) {
        this.id = id;
        this.flavor = flavor;
        this.codeModel = codeModel;
    }

    private boolean needsModel() {
        return flavor == Flavor.COMPONENT || flavor == Flavor.TRANSARC;
    }

    private boolean needsCode() {
        return flavor != Flavor.COMPONENT;
    }

    private CodeModelClient codeModel() {
        return codeModel != null ? codeModel : CodeModelClient.fromEnv();
    }

    @Override
    public String id() {
        return id;
    }

    @Override
    public String version() {
        return VERSION;
    }

    @Override
    public Set<KindPair> supports() {
        return switch (flavor) {
            case COMPONENT -> Set.of(new KindPair("architecture-doc", "architecture-model"));
            case TRANSARC ->
                Set.of(new KindPair("architecture-doc", "code"), new KindPair("architecture-model", "code"));
            default -> Set.of(new KindPair("architecture-doc", "code"));
        };
    }

    @Override
    public boolean usesLlm() {
        return true;
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
    public String environment(TlrContext ctx) {
        try {
            var env = JsonMapper.shared().createObjectNode();
            env.put("chatModel", ctx.llm().chatModel());
            env.put("embedModel", ctx.llm().embedModel());
            env.put("seed", ArtemisGateway.SEED);
            if (needsCode()) {
                try (CodeModelClient client = codeModel()) {
                    env.put("codeModel", client.identity());
                }
            }
            return env.toString();
        } catch (LlmUnavailableException e) {
            throw new ApproachUnavailableException(e.getMessage(), e);
        }
    }

    @Override
    public List<CandidateLink> recover(TlrInput in, TlrContext ctx) {
        try {
            return recoverUnwrapped(in, ctx);
        } catch (LlmUnavailableException e) {
            throw new ApproachUnavailableException(e.getMessage(), e);
        }
    }

    private List<CandidateLink> recoverUnwrapped(TlrInput in, TlrContext ctx) {
        List<TlrInput.Version> docs = versions(in, "architecture-doc", TlrInput.Version::id);
        List<TlrInput.Version> models =
                needsModel() ? versions(in, "architecture-model", TlrInput.Version::id) : List.of();
        List<TlrInput.Version> code = needsCode() ? versions(in, "code", TlrInput.Version::path) : List.of();
        Map<String, CandidateLink> links = new LinkedHashMap<>();
        if (docs.isEmpty() || (needsModel() && models.isEmpty()) || (needsCode() && code.isEmpty())) {
            return new ArrayList<>(links.values());
        }
        Map<String, TlrInput.Element> files = new LinkedHashMap<>();
        for (TlrInput.Element t : in.targets()) {
            if ("file".equals(t.kind())
                    && (flavor == Flavor.DATAFILE || "CodeUnit".equals(t.label()))
                    && files.put(t.localKey(), t) != null) {
                throw new IllegalStateException("two file elements with local key " + t.localKey());
            }
        }
        if (ctx.artifacts() == null) {
            throw new IllegalStateException("no artifact store configured, cannot read the inputs");
        }
        Path dir = null;
        try {
            dir = Files.createTempDirectory("artemis-");
            ArdocodeApproach.Acm acm = null;
            for (TlrInput.Version doc : docs) {
                List<TlrInput.Element> sentences = SentenceRendering.sentences(in.sources(), doc.id());
                if (sentences.isEmpty()) {
                    ctx.log().warn("{}: document version {} has no sentences, skipped", id, doc.id());
                    continue;
                }
                SentenceRendering.assertHashes(sentences);
                if (needsCode() && acm == null) {
                    acm = ArdocodeApproach.fetchAcm(codeModel, in, code, ctx, dir);
                }
                if (!needsModel()) {
                    run(in, ctx, dir, acm, sentences, null, null, files, links);
                    continue;
                }
                for (TlrInput.Version model : models) {
                    if (!model.pairsWithDoc(doc.id())) {
                        continue;
                    }
                    ModelFormat format = ArdocoApproach.format(model);
                    if (format == null) {
                        ctx.log()
                                .warn(
                                        "{}: model {} has unsupported format {}, skipped",
                                        id,
                                        model.path(),
                                        model.format());
                        continue;
                    }
                    run(in, ctx, dir, acm, sentences, model, format, files, links);
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

    private record Out(
            List<Map.Entry<Integer, String>> sadModel,
            List<Map.Entry<Integer, String>> sadCode,
            List<Map.Entry<String, String>> samCode) {}

    private void run(
            TlrInput in,
            TlrContext ctx,
            Path dir,
            ArdocodeApproach.Acm acm,
            List<TlrInput.Element> sentences,
            TlrInput.Version model,
            ModelFormat format,
            Map<String, TlrInput.Element> files,
            Map<String, CandidateLink> links)
            throws IOException {
        if (ctx.cancelled().getAsBoolean()) {
            throw new CancellationException("cancelled");
        }
        Map<String, TlrInput.Element> modelElements = new LinkedHashMap<>();
        if (model != null) {
            for (TlrInput.Element e :
                    Stream.concat(in.sources().stream(), in.targets().stream()).toList()) {
                if (model.id().equals(e.versionId()) && modelElements.put(e.localKey(), e) != null) {
                    throw new IllegalStateException(
                            "model " + model.path() + " has two elements with local key " + e.localKey());
                }
            }
        }
        Path run = Files.createTempDirectory(dir, "run-");
        Path modelFile = model != null ? ArdocoRuns.stageModel(ctx, run, model) : null;
        ctx.progress().accept(id + ": running ArTEMiS on " + sentences.size() + " sentences");
        ArdocoRuns.acquire(ctx);
        Out out;
        try {
            long start = System.nanoTime();
            ArdocoRuns.resetHeapPeak();
            out = execute(ctx, sentences, modelFile, format, acm);
            ctx.log()
                    .info(
                            "{} run: {} sentences, {} SAD-model, {} SAD-code and {} SAM-code pairs, {} ms, peak heap {} MiB",
                            id,
                            sentences.size(),
                            out.sadModel().size(),
                            out.sadCode().size(),
                            out.samCode().size(),
                            (System.nanoTime() - start) / 1_000_000,
                            ArdocoRuns.peakHeapMiB());
        } finally {
            ArdocoRuns.LOCK.unlock();
        }
        String evidence = "target=" + flavor.target;
        for (var pair : sortedDistinct(out.sadModel())) {
            TlrInput.Element target = modelElements.get(pair.getValue());
            if (target == null || pair.getKey() < 0 || pair.getKey() >= sentences.size()) {
                throw new IllegalStateException("ArTEMiS link to unknown model id " + pair.getValue() + " or sentence "
                        + pair.getKey() + " (model " + model.path() + ")");
            }
            put(links, new CandidateLink(sentences.get(pair.getKey()).id(), target.id(), "documents", 1.0, evidence));
        }
        List<Map.Entry<Integer, String>> sadCode = out.sadCode();
        if (flavor == Flavor.DATAFILE) {
            sadCode = sadCode.stream()
                    .filter(p -> {
                        boolean known = files.containsKey(p.getValue());
                        if (!known) {
                            ctx.log().warn("{}: no element for file {}, link skipped", id, p.getValue());
                        }
                        return known;
                    })
                    .toList();
        }
        for (CandidateLink l : ArdocodeApproach.toLinks(sadCode, sentences, files)) {
            put(links, new CandidateLink(l.sourceId(), l.targetId(), l.linkType(), l.confidence(), evidence));
        }
        if (!out.samCode().isEmpty()) {
            TransarcApproach.samToLinks(out.samCode(), modelElements, files, model)
                    .forEach(l -> put(links, l));
        }
    }

    private static void put(Map<String, CandidateLink> links, CandidateLink l) {
        links.put(l.sourceId() + ">" + l.targetId(), l);
    }

    private static List<Map.Entry<Integer, String>> sortedDistinct(List<Map.Entry<Integer, String>> pairs) {
        return pairs.stream()
                .distinct()
                .sorted(Map.Entry.<Integer, String>comparingByKey().thenComparing(Map.Entry.comparingByValue()))
                .toList();
    }

    private Out execute(
            TlrContext ctx,
            List<TlrInput.Element> sentences,
            Path modelFile,
            ModelFormat format,
            ArdocodeApproach.Acm acm)
            throws IOException {
        var ardoco = new Ardoco("sandman-artemis");
        DataRepository repo = ardoco.getDataRepository();
        DataRepositoryHelper.putInputText(
                repo,
                String.join(
                        "\n",
                        SentenceRendering.lines(sentences).stream()
                                .map(ArtemisGateway::terminated)
                                .toList()));
        ardoco.addPipelineStep(SimpleTextPreprocessingAgent.get(ArtemisGateway.NO_CONFIG, repo));
        if (needsCode()) {
            var mapper = JsonHandling.createObjectMapper();
            mapper.registerModule(new Jdk8Module());
            CodeModel code = new CodeModelWithCompilationUnits(
                    mapper.readValue(acm.file().toFile(), CodeModel.CodeModelDto.class));
            var states = new ModelStates();
            states.addModel(Metamodel.CODE_WITH_COMPILATION_UNITS, code);
            repo.addData(ModelStates.ID, states);
            IdentifierProvider.reset();
        }
        if (needsModel()) {
            ardoco.addPipelineStep(ModelProviderAgent.getModelProviderAgent(
                    repo,
                    ArtemisGateway.NO_CONFIG,
                    new ArchitectureConfiguration(modelFile.toFile(), format, Metamodel.ARCHITECTURE_WITH_COMPONENTS),
                    null));
        }
        if (flavor == Flavor.EXARCH) {
            ardoco.addPipelineStep(ArtemisGateway.docArchitecture(repo, ctx.llmClient(), ctx.llm()));
        }
        ArtemisNerStrategy strategy =
                switch (flavor) {
                    case CLASS -> new ClassArtemisNerStrategy();
                    case DATAFILE -> new DatafileArtemisNerStrategy();
                    default -> new ComponentArtemisNerStrategy();
                };
        ardoco.addPipelineStep(ArtemisGateway.stage(repo, List.of(strategy), ctx.llmClient(), ctx.llm()));
        if (flavor == Flavor.TRANSARC || flavor == Flavor.EXARCH) {
            ardoco.addPipelineStep(SamCodeTraceabilityLinkRecovery.get(ArtemisGateway.NO_CONFIG, repo));
            ardoco.addPipelineStep(SadSamCodeTraceabilityLinkRecovery.get(ArtemisGateway.NO_CONFIG, repo));
        }
        ardoco.run();
        var result = new ArdocoResult(repo);
        List<Map.Entry<Integer, String>> sadModel = new ArrayList<>();
        List<Map.Entry<Integer, String>> sadCode = new ArrayList<>();
        List<Map.Entry<String, String>> samCode = new ArrayList<>();
        switch (flavor) {
            case COMPONENT -> {
                for (var tl :
                        result.getArtemisConnectionState(strategy.getTarget()).getTraceLinks()) {
                    sadModel.add(Map.entry(
                            tl.getFirstEndpoint().getSentenceNumber() - 1,
                            tl.getSecondEndpoint().getId()));
                }
            }
            case CLASS -> {
                for (var tl :
                        result.getArtemisConnectionState(strategy.getTarget()).getTraceLinks()) {
                    CodeCompilationUnit unit = ((ClassUnit) tl.getSecondEndpoint()).getCompilationUnit();
                    if (unit != null) {
                        sadCode.add(Map.entry(
                                tl.getFirstEndpoint().getSentenceNumber() - 1, ArdocodeApproach.pathOf(unit)));
                    }
                }
            }
            case DATAFILE -> {
                for (var tl :
                        result.getArtemisConnectionState(strategy.getTarget()).getTraceLinks()) {
                    sadCode.add(Map.entry(
                            tl.getFirstEndpoint().getSentenceNumber() - 1,
                            ((CodeFile) tl.getSecondEndpoint()).getPath()));
                }
            }
            default -> {
                for (var tl : result.getSadCodeTraceLinks()) {
                    sadCode.add(Map.entry(
                            tl.getFirstEndpoint().getSentence().getSentenceNumber(),
                            ArdocodeApproach.pathOf((CodeCompilationUnit) tl.getSecondEndpoint())));
                }
                if (flavor == Flavor.TRANSARC) {
                    for (var tl : result.getSamCodeTraceLinks()) {
                        samCode.add(
                                Map.entry(tl.getFirstEndpoint().getId(), ArdocodeApproach.pathOf((CodeCompilationUnit)
                                        tl.getSecondEndpoint())));
                    }
                }
            }
        }
        return new Out(sadModel, sadCode, samCode);
    }
}
