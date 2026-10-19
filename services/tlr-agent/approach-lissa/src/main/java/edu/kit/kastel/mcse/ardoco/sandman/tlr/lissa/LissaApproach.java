package edu.kit.kastel.mcse.ardoco.sandman.tlr.lissa;

import edu.kit.kastel.mcse.ardoco.sandman.agent.Ids;
import edu.kit.kastel.mcse.ardoco.sandman.agent.LlmAccess;
import edu.kit.kastel.mcse.ardoco.sandman.agent.LlmUnavailableException;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.ApproachUnavailableException;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.CandidateLink;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.KindPair;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.ScopeMode;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.TlrContext;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.TlrInput;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.TraceLinkApproach;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.lissa.classifier.Classifier;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.lissa.classifier.LissaChat;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.lissa.classifier.ReasoningClassifier;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.lissa.classifier.ReasoningClassifierPrompt;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.lissa.classifier.SimpleClassifier;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.lissa.elementstore.SourceElementStore;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.lissa.elementstore.TargetElementStore;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.lissa.elementstore.strategy.CosineSimilarity;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.lissa.embedding.LissaEmbedder;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.lissa.knowledge.Artifact;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.lissa.knowledge.Element;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.lissa.knowledge.TraceLink;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.lissa.preprocessor.SingleArtifactPreprocessor;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.lissa.resultaggregator.AnyResultAggregator;
import java.io.IOException;
import java.io.UncheckedIOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.Comparator;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.atomic.AtomicInteger;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

/**
 * LiSSA (ArDoCo/LiSSA-RATLR) adapted to Sandman (T2.2): requirements to code files. Embedding retrieval with k
 * candidates per requirement, then an LLM yes/no classification of every candidate pair. All LLM calls go through
 * the Sandman gateway ({@code LlmClient}), so they are budgeted, recorded and replayable.
 */
public final class LissaApproach implements TraceLinkApproach {

    public static final String ID = "lissa";
    /** Raise the +sN suffix when the mapping of LiSSA results to links changes. */
    public static final String VERSION = "1.1.0+s1";
    /** Commit of ArDoCo/LiSSA-RATLR the vendored classes come from; equals the file LISSA_COMMIT. */
    public static final String LISSA_COMMIT = "18ed9365c626d5919790a85d87c675c766c22abb";

    public static final int DEFAULT_K = 20;
    public static final int DEFAULT_CONCURRENCY = 8;
    public static final double TEMPERATURE = 0.0;
    public static final int SEED = 133742243;

    private static final String SIMPLE = SimpleClassifier.SIMPLE_CLASSIFIER_NAME;
    private static final String REASONING = ReasoningClassifier.REASONING_CLASSIFIER_NAME;

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
        return Set.of(new KindPair("requirements", "code"));
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
        return Set.of("retrieval", "classifier", "concurrency");
    }

    @Override
    public String environment(TlrContext ctx) {
        LlmAccess llm = ctx.llm();
        ObjectNode env = JsonMapper.shared().createObjectNode();
        env.put("chatModel", llm.chatModel());
        env.put("embedModel", llm.embedModel());
        env.put("classifier", promptId(ctx));
        env.put("temperature", TEMPERATURE);
        env.put("seed", SEED);
        env.put("k", k(ctx.params()));
        env.put("lissaCommit", LISSA_COMMIT);
        return env.toString();
    }

    @Override
    public List<String> promptIds(TlrContext ctx) {
        return List.of(promptId(ctx));
    }

    @Override
    public List<CandidateLink> recover(TlrInput in, TlrContext ctx) {
        List<Element> sources = sourceElements(in);
        List<Element> targets = targetElements(in, ctx);
        if (sources.isEmpty() || targets.isEmpty()) {
            return List.of();
        }
        try {
            return classify(sources, targets, ctx);
        } catch (LlmUnavailableException e) {
            throw new ApproachUnavailableException(e.getMessage(), e);
        }
    }

    private List<CandidateLink> classify(List<Element> sources, List<Element> targets, TlrContext ctx) {
        int concurrency = concurrency(ctx.params());
        LlmAccess llm = ctx.llm();
        LissaEmbedder embedder = new LissaEmbedder(
                ctx.llmClient(),
                llm.embedRoute(),
                concurrency,
                ctx.cancelled(),
                w -> ctx.log().warn("lissa: {}", w));
        ctx.progress()
                .accept("lissa: embedding " + sources.size() + " requirements and " + targets.size() + " code files");
        var sourceStore = new SourceElementStore(embedder.embed(sources));
        var targetStore = new TargetElementStore(embedder.embed(targets), new CosineSimilarity(k(ctx.params())));
        int pairs = sources.size() * Math.min(k(ctx.params()), targets.size());
        AtomicInteger done = new AtomicInteger();
        var chat = new LissaChat(
                ctx.llmClient(),
                llm.chatRoute(),
                TEMPERATURE,
                SEED,
                () -> ctx.progress()
                        .accept("lissa: calling LLM for link classification " + done.incrementAndGet() + "/" + pairs));
        Classifier classifier = classifier(ctx.params(), concurrency, ctx, chat);
        var results = classifier.classify(sourceStore, targetStore);
        Set<TraceLink> links = new AnyResultAggregator().aggregate(sources, targets, results);
        return links.stream()
                .sorted(Comparator.comparing(TraceLink::sourceId).thenComparing(TraceLink::targetId))
                .map(l -> new CandidateLink(l.sourceId(), l.targetId(), "implements", 1.0, null))
                .toList();
    }

    private static Classifier classifier(JsonNode params, int concurrency, TlrContext ctx, LissaChat chat) {
        return switch (classifierName(params)) {
            case SIMPLE -> new SimpleClassifier(concurrency, ctx.cancelled(), chat, SimpleClassifier.DEFAULT_TEMPLATE);
            case REASONING -> new ReasoningClassifier(concurrency, ctx.cancelled(), chat, reasoningTemplate());
            default -> throw new IllegalStateException("unreachable");
        };
    }

    private static String reasoningTemplate() {
        return ReasoningClassifierPrompt.REASON_WITH_NAME.getPromptTemplate();
    }

    private static String classifierName(JsonNode params) {
        String name = params.path("classifier").asString(SIMPLE);
        if (!SIMPLE.equals(name) && !REASONING.equals(name)) {
            throw new IllegalArgumentException("lissa classifier must be '" + SIMPLE + "' or '" + REASONING + "'");
        }
        return name;
    }

    private static String promptId(TlrContext ctx) {
        String name = classifierName(ctx.params());
        String template = SIMPLE.equals(name) ? SimpleClassifier.DEFAULT_TEMPLATE : reasoningTemplate();
        return "lissa-" + name + "@" + Ids.sha256Hex(template).substring(0, 8);
    }

    static int k(JsonNode params) {
        int k = params.path("retrieval").path("k").asInt(DEFAULT_K);
        if (k < 1) {
            throw new IllegalArgumentException("lissa retrieval.k must be at least 1");
        }
        return k;
    }

    static int concurrency(JsonNode params) {
        int concurrency = params.path("concurrency").asInt(DEFAULT_CONCURRENCY);
        if (concurrency < 1) {
            throw new IllegalArgumentException("lissa concurrency must be at least 1");
        }
        return concurrency;
    }

    private static List<Element> sourceElements(TlrInput in) {
        return in.sources().stream()
                .filter(e -> "Requirement".equals(e.label()))
                .sorted(Comparator.comparing(TlrInput.Element::id))
                .map(e -> toElement(e.id(), Artifact.ArtifactType.REQUIREMENT, e.text() == null ? "" : e.text()))
                .toList();
    }

    private List<Element> targetElements(TlrInput in, TlrContext ctx) {
        Map<String, TlrInput.Version> versions = new HashMap<>();
        in.versions().forEach(v -> versions.put(v.id(), v));
        return in.targets().stream()
                .filter(e -> "CodeUnit".equals(e.label()) && "file".equals(e.kind()))
                .sorted(Comparator.comparing(TlrInput.Element::id))
                .map(e ->
                        toElement(e.id(), Artifact.ArtifactType.SOURCE_CODE, read(versions.get(e.versionId()), e, ctx)))
                .toList();
    }

    private static Element toElement(String id, Artifact.ArtifactType type, String content) {
        return new SingleArtifactPreprocessor()
                .preprocess(List.of(new Artifact(id, type, content)))
                .get(0);
    }

    private static String read(TlrInput.Version version, TlrInput.Element element, TlrContext ctx) {
        if (ctx.artifacts() == null) {
            throw new IllegalStateException("no artifact store configured, cannot read " + element.id());
        }
        if (version == null) {
            throw new IllegalStateException("no version " + element.versionId() + " for element " + element.id());
        }
        try {
            return Files.readString(ctx.artifacts().pathFor(version.storageUri()), StandardCharsets.UTF_8)
                    .replace("\r\n", "\n");
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }
    }
}
