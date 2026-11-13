package edu.kit.kastel.mcse.ardoco.sandman.inconsistency.ardoco;

import edu.kit.kastel.mcse.ardoco.core.api.models.ModelFormat;
import edu.kit.kastel.mcse.ardoco.sandman.agent.LlmAccess;
import edu.kit.kastel.mcse.ardoco.sandman.agent.LlmClient;
import edu.kit.kastel.mcse.ardoco.sandman.agent.Settings;
import edu.kit.kastel.mcse.ardoco.sandman.ardocotext.SentenceRendering;
import edu.kit.kastel.mcse.ardoco.sandman.inconsistency.DetectionScope;
import edu.kit.kastel.mcse.ardoco.sandman.inconsistency.DetectorContext;
import edu.kit.kastel.mcse.ardoco.sandman.inconsistency.Finding;
import edu.kit.kastel.mcse.ardoco.sandman.inconsistency.InconsistencyDetector;
import java.io.IOException;
import java.io.UncheckedIOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.function.Function;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * ArDoCo Artemis inconsistency detection (T3.2, T3.9): named entities found by an LLM through the gateway, linked to the
 * model (components) or to the code (classes, data files), unlinked ones reported. Components also report model
 * elements that the text does not mention. Findings: see {@link Mapping}.
 */
public final class ArtemisIdDetector implements InconsistencyDetector {

    public static final String ID = "artemis-id";
    /** Raise the first part when the mapping of Artemis results to findings changes. */
    public static final String VERSION = "0.2.0+21ef682";

    private static final Logger LOG = LoggerFactory.getLogger(ArtemisIdDetector.class);

    /** Supplies the ACM (JSON code model) of the current code files for the class and datafile strategies. */
    @FunctionalInterface
    public interface CodeModelSource {
        record CodeFile(String path, String storagePath) {}

        /** Writes the ACM below {@code dir} and returns its path. */
        Path acm(String projectId, List<CodeFile> files, Path dir) throws IOException;
    }

    private final Function<String, LlmAccess> access;
    private final CodeModelSource codeModel;

    /** ServiceLoader entry: the gateway settings are read from the environment when a run needs them. */
    public ArtemisIdDetector() {
        this(policy -> LlmAccess.forPolicy(Settings.fromEnv(), policy), PluginCodeModelSource.fromEnv());
    }

    public ArtemisIdDetector(Function<String, LlmAccess> access) {
        this(access, null);
    }

    /** {@code codeModel == null}: no ACM source is wired, the class and datafile strategies are skipped with a warning. */
    public ArtemisIdDetector(Function<String, LlmAccess> access, CodeModelSource codeModel) {
        this.access = access;
        this.codeModel = codeModel;
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
    public Set<String> types() {
        return Set.of(Mapping.UNDOCUMENTED, Mapping.MISSING);
    }

    @Override
    public Set<String> paramKeys() {
        return Set.of("textKinds", "modelKinds", "codeKinds", "strategies");
    }

    @Override
    public ScopeMode scopeMode() {
        return ScopeMode.WHOLE_PROJECT;
    }

    @Override
    public boolean usesLlm() {
        return true;
    }

    @Override
    public String description() {
        return "ArDoCo Artemis inconsistency detection (LLM): undocumented model components and text entities (components, classes, data files) missing from the model or code";
    }

    @Override
    public String configIdentity(DetectorContext ctx) {
        try {
            LlmAccess a = access.apply(ctx.llmPolicy());
            return a.chatModel() + "|" + a.embedModel();
        } catch (RuntimeException e) {
            return "llm-unavailable";
        }
    }

    @Override
    public List<Finding> detect(DetectionScope scope, DetectorContext ctx) {
        return detectWithWarnings(scope, ctx).findings();
    }

    @Override
    public Detection detectWithWarnings(DetectionScope scope, DetectorContext ctx) {
        List<String> textKinds = ArdocoIdDetector.strings(ctx.params().get("textKinds"), List.of("architecture-doc"));
        List<String> modelKinds =
                ArdocoIdDetector.strings(ctx.params().get("modelKinds"), List.of("architecture-model"));
        List<String> codeKinds = ArdocoIdDetector.strings(ctx.params().get("codeKinds"), List.of("code"));
        List<String> targets = ArdocoIdDetector.strings(ctx.params().get("strategies"), ArtemisRunner.ALL_TARGETS);
        for (String t : targets) {
            if (!ArtemisRunner.ALL_TARGETS.contains(t)) {
                throw new IllegalArgumentException(
                        "unknown strategy " + t + ", expected one of " + ArtemisRunner.ALL_TARGETS);
            }
        }
        if (ctx.artifacts() == null) {
            throw new IllegalStateException("no artifact store configured");
        }
        LlmAccess llm = access.apply(ctx.llmPolicy());
        KgInput kg = new KgInput(ctx.kg());
        List<String> warnings = new ArrayList<>();
        targets = new ArrayList<>(targets);
        List<KgInput.Version> code = List.of();
        boolean wantsCode = targets.contains(ArtemisRunner.CLASS) || targets.contains(ArtemisRunner.DATAFILE);
        if (wantsCode) {
            code = kg.currentVersions(scope.projectId(), codeKinds);
            if (codeModel == null || code.isEmpty()) {
                warnings.add("strategies class and datafile skipped: "
                        + (codeModel == null ? "no code model source is configured" : "the project has no code"));
                targets.remove(ArtemisRunner.CLASS);
                targets.remove(ArtemisRunner.DATAFILE);
            }
        }
        List<KgInput.Version> models = targets.contains(ArtemisRunner.COMPONENT)
                ? kg.currentVersions(scope.projectId(), modelKinds)
                : List.of();
        if (targets.contains(ArtemisRunner.COMPONENT) && models.isEmpty()) {
            warnings.add("strategy component skipped: the project has no architecture model");
            targets.remove(ArtemisRunner.COMPONENT);
        }
        if (targets.isEmpty()) {
            return new Detection(List.of(), warnings);
        }
        LlmClient client = new LlmClient(llm, "artemis-id-" + scope.projectId());
        List<Mapping.RunResult> results = new ArrayList<>();
        Path acmDir = null;
        try {
            Path acm = null;
            if (targets.contains(ArtemisRunner.CLASS) || targets.contains(ArtemisRunner.DATAFILE)) {
                acmDir = Files.createTempDirectory("artemis-acm-");
                acm = codeModel.acm(
                        scope.projectId(),
                        code.stream()
                                .map(v -> new CodeModelSource.CodeFile(
                                        v.path(),
                                        ctx.artifacts().pathFor(v.storageUri()).toString()))
                                .toList(),
                        acmDir);
            }
            for (KgInput.Version doc : kg.currentVersions(scope.projectId(), textKinds)) {
                List<KgInput.Sentence> sentences = kg.sentences(doc.id());
                if (sentences.isEmpty()) {
                    warnings.add("document version " + doc.id() + " has no sentences, skipped");
                    continue;
                }
                for (int i = 0; i < sentences.size(); i++) {
                    var s = sentences.get(i);
                    SentenceRendering.assertHash(i, doc.id(), s.localKey(), s.text());
                }
                List<String> lines = sentences.stream()
                        .map(s -> SentenceRendering.line(s.text()))
                        .toList();
                if (!targets.contains(ArtemisRunner.COMPONENT)) {
                    ArdocoIdDetector.cancelCheck(ctx);
                    results.add(runPair(ctx, kg, doc, sentences, lines, null, null, acm, targets, client, llm));
                    continue;
                }
                for (KgInput.Version model : models) {
                    ModelFormat format = ArdocoIdDetector.format(model);
                    if (format == null) {
                        warnings.add(
                                "model " + model.path() + " has unsupported format " + model.format() + ", skipped");
                        continue;
                    }
                    ArdocoIdDetector.cancelCheck(ctx);
                    results.add(runPair(ctx, kg, doc, sentences, lines, model, format, acm, targets, client, llm));
                }
            }
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        } finally {
            if (acmDir != null) {
                ArdocoIdDetector.delete(acmDir);
            }
        }
        warnings.add("LLM tokens: " + client.inputTokens() + " in, " + client.outputTokens() + " out");
        return new Detection(Mapping.map(results), warnings);
    }

    private static Mapping.RunResult runPair(
            DetectorContext ctx,
            KgInput kg,
            KgInput.Version doc,
            List<KgInput.Sentence> sentences,
            List<String> lines,
            KgInput.Version model,
            ModelFormat format,
            Path acm,
            List<String> targets,
            LlmClient client,
            LlmAccess llm) {
        Map<String, Mapping.ModelEl> byLocalKey = new LinkedHashMap<>();
        if (model != null) {
            for (KgInput.ModelElement e : kg.modelElements(model.id())) {
                if (byLocalKey.put(e.localKey(), new Mapping.ModelEl(e.key(), e.name(), e.inferred())) != null) {
                    throw new IllegalStateException(
                            "model " + model.path() + " has two elements with local key " + e.localKey());
                }
            }
        }
        ArdocoIdDetector.acquire(ctx);
        Path dir = null;
        try {
            dir = Files.createTempDirectory("artemis-id-");
            Path modelFile = null;
            if (model != null) {
                modelFile = dir.resolve(Path.of(model.path()).getFileName().toString());
                Files.copy(ctx.artifacts().pathFor(model.storageUri()), modelFile);
            }
            long start = System.nanoTime();
            var raw = ArtemisRunner.run(lines, modelFile, format, acm, targets, client, llm);
            LOG.info(
                    "artemis-id run {}: doc {} x model {}: {} model and {} text findings, {} ms",
                    targets,
                    doc.path(),
                    model == null ? "none" : model.path(),
                    raw.models().size(),
                    raw.words().size(),
                    (System.nanoTime() - start) / 1_000_000);
            return new Mapping.RunResult(
                    doc.id(),
                    model == null ? null : model.id(),
                    sentences.stream().map(s -> new Mapping.Sentence(s.key())).toList(),
                    byLocalKey,
                    raw);
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        } finally {
            IdRunner.LOCK.unlock();
            if (dir != null) {
                ArdocoIdDetector.delete(dir);
            }
        }
    }
}
