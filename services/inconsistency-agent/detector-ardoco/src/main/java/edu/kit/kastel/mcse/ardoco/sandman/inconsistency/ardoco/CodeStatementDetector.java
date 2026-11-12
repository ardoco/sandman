package edu.kit.kastel.mcse.ardoco.sandman.inconsistency.ardoco;

import dev.langchain4j.model.chat.ChatModel;
import dev.langchain4j.service.AiServices;
import edu.kit.kastel.mcse.ardoco.core.common.readonlytools.CodeDirectoryReadOnlyTools;
import edu.kit.kastel.mcse.ardoco.core.data.DataRepository;
import edu.kit.kastel.mcse.ardoco.naer.model.NamedEntity;
import edu.kit.kastel.mcse.ardoco.naer.model.SoftwareArchitectureDocumentation;
import edu.kit.kastel.mcse.ardoco.naer.recognizer.NamedEntityRecognizer;
import edu.kit.kastel.mcse.ardoco.sandman.agent.LlmAccess;
import edu.kit.kastel.mcse.ardoco.sandman.agent.LlmClient;
import edu.kit.kastel.mcse.ardoco.sandman.agent.Settings;
import edu.kit.kastel.mcse.ardoco.sandman.ardocotext.SentenceRendering;
import edu.kit.kastel.mcse.ardoco.sandman.inconsistency.DetectionScope;
import edu.kit.kastel.mcse.ardoco.sandman.inconsistency.DetectorContext;
import edu.kit.kastel.mcse.ardoco.sandman.inconsistency.Finding;
import edu.kit.kastel.mcse.ardoco.sandman.inconsistency.InconsistencyDetector;
import edu.kit.kastel.mcse.ardoco.tlr.artemis.strategies.ArtemisNerStrategy;
import java.io.IOException;
import java.io.UncheckedIOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;
import java.util.function.Function;

/**
 * Statement-level ArDoCo Artemis checks (T3.9): an LLM finds statements about a programming language or an
 * architectural style, a second LLM agent with read-only tools checks each statement against the code files. A finding
 * has no model element: it points at the sentence, the evidence holds the statements and the explanations. One finding
 * per sentence, so the fingerprint (type, detector, sentence) stays unique.
 */
abstract class CodeStatementDetector implements InconsistencyDetector {

    /** The verdict of the validation agent for one statement. */
    record Verdict(boolean valid, String explanation) {}

    /** Asks the validation agent about one statement. */
    @FunctionalInterface
    interface Validator {
        Verdict validate(String occurrence, String name, String fullText, String line, String codePath);
    }

    private static final int RETRIES = 3;

    private final Function<String, LlmAccess> access;
    private final String id;
    private final String version;
    private final String type;
    private final String subject;
    private final String description;

    CodeStatementDetector(
            Function<String, LlmAccess> access,
            String id,
            String version,
            String type,
            String subject,
            String description) {
        this.access = access;
        this.id = id;
        this.version = version;
        this.type = type;
        this.subject = subject;
        this.description = description;
    }

    static Function<String, LlmAccess> envAccess() {
        return policy -> LlmAccess.forPolicy(Settings.fromEnv(), policy);
    }

    abstract ArtemisNerStrategy strategy();

    abstract Validator validator(ChatModel model, Path codeDir);

    @Override
    public final String id() {
        return id;
    }

    @Override
    public final String version() {
        return version;
    }

    @Override
    public final Set<String> types() {
        return Set.of(type);
    }

    @Override
    public final Set<String> paramKeys() {
        return Set.of("textKinds", "codeKinds");
    }

    @Override
    public final ScopeMode scopeMode() {
        return ScopeMode.WHOLE_PROJECT;
    }

    @Override
    public final boolean usesLlm() {
        return true;
    }

    @Override
    public final String description() {
        return description;
    }

    @Override
    public final String configIdentity(DetectorContext ctx) {
        try {
            LlmAccess a = access.apply(ctx.llmPolicy());
            return a.chatModel();
        } catch (RuntimeException e) {
            return "llm-unavailable";
        }
    }

    @Override
    public final List<Finding> detect(DetectionScope scope, DetectorContext ctx) {
        return detectWithWarnings(scope, ctx).findings();
    }

    @Override
    public final Detection detectWithWarnings(DetectionScope scope, DetectorContext ctx) {
        List<String> textKinds = ArdocoIdDetector.strings(ctx.params().get("textKinds"), List.of("architecture-doc"));
        List<String> codeKinds = ArdocoIdDetector.strings(ctx.params().get("codeKinds"), List.of("code"));
        if (ctx.artifacts() == null) {
            throw new IllegalStateException("no artifact store configured");
        }
        LlmAccess llm = access.apply(ctx.llmPolicy());
        KgInput kg = new KgInput(ctx.kg());
        List<String> warnings = new ArrayList<>();
        List<KgInput.Version> code = kg.currentVersions(scope.projectId(), codeKinds);
        if (code.isEmpty()) {
            warnings.add("skipped: the project has no code");
            return new Detection(List.of(), warnings);
        }
        LlmClient client = new LlmClient(llm, id + "-" + scope.projectId());
        ChatModel model = new ArtemisChatModel(client, llm);
        List<Finding> findings = new ArrayList<>();
        Path dir = null;
        try {
            dir = Files.createTempDirectory("code-statement-");
            for (KgInput.Version v : code) {
                Path target = dir.resolve(v.path()).normalize();
                if (!target.startsWith(dir)) {
                    throw new IllegalStateException("code path leaves the code directory: " + v.path());
                }
                Files.createDirectories(target.getParent());
                Files.copy(ctx.artifacts().pathFor(v.storageUri()), target);
            }
            Validator validator = validator(model, dir);
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
                ArdocoIdDetector.cancelCheck(ctx);
                findings.addAll(check(ctx, doc, sentences, model, validator, dir));
            }
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        } finally {
            if (dir != null) {
                ArdocoIdDetector.delete(dir);
            }
        }
        warnings.add("LLM tokens: " + client.inputTokens() + " in, " + client.outputTokens() + " out");
        return new Detection(findings, warnings);
    }

    private List<Finding> check(
            DetectorContext ctx,
            KgInput.Version doc,
            List<KgInput.Sentence> sentences,
            ChatModel model,
            Validator validator,
            Path codeDir) {
        List<String> lines = sentences.stream()
                .map(s -> ArtemisRunner.terminated(SentenceRendering.line(s.text())))
                .toList();
        String fullText = String.join(System.lineSeparator(), lines);
        ArdocoIdDetector.acquire(ctx);
        List<NamedEntity> entities;
        try {
            entities = new ArrayList<>(new NamedEntityRecognizer.Builder()
                    .chatModel(model)
                    .prompt(strategy().createPrompt(new DataRepository()))
                    .build()
                    .recognize(new SoftwareArchitectureDocumentation(fullText)));
            entities.sort(java.util.Comparator.comparing(NamedEntity::getName));
        } finally {
            IdRunner.LOCK.unlock();
        }
        Map<Integer, List<Map<String, Object>>> bySentence = new TreeMap<>();
        for (NamedEntity entity : entities) {
            for (int line : new java.util.TreeSet<>(entity.getOccurrenceLines())) {
                if (line < 1 || line > lines.size()) {
                    continue;
                }
                ArdocoIdDetector.cancelCheck(ctx);
                Verdict verdict = validate(validator, entity.getName(), fullText, lines.get(line - 1), line, codeDir);
                if (!verdict.valid()) {
                    Map<String, Object> statement = new LinkedHashMap<>();
                    statement.put("statement", entity.getName());
                    statement.put("explanation", verdict.explanation());
                    bySentence.computeIfAbsent(line, k -> new ArrayList<>()).add(statement);
                }
            }
        }
        List<Finding> out = new ArrayList<>();
        for (var e : bySentence.entrySet()) {
            var statements = e.getValue();
            statements.sort(java.util.Comparator.comparing(m -> (String) m.get("statement")));
            String names = String.join(
                    ", ",
                    statements.stream()
                            .map(m -> (String) m.get("statement"))
                            .distinct()
                            .toList());
            Map<String, Object> evidence = new LinkedHashMap<>();
            evidence.put("docVersionId", doc.id());
            evidence.put("sentence", e.getKey());
            evidence.put("sentenceText", sentences.get(e.getKey() - 1).text());
            evidence.put("statements", statements);
            String title = "Sentence states a " + subject + " that the code contradicts: " + names;
            out.add(new Finding(
                    type,
                    "warning",
                    title.substring(0, Math.min(200, title.length())),
                    (String) statements.get(0).get("explanation"),
                    1.0,
                    List.of(Finding.Involved.element(
                            sentences.get(e.getKey() - 1).key(), "subject")),
                    evidence));
        }
        return out;
    }

    private static Verdict validate(
            Validator validator, String name, String fullText, String line, int sentence, Path codeDir) {
        RuntimeException last = null;
        for (int attempt = 1; attempt <= RETRIES; attempt++) {
            try {
                return validator.validate(sentence + ":" + name, name, fullText, line, codeDir.toString());
            } catch (IllegalArgumentException e) {
                last = e;
            }
        }
        throw last;
    }

    /** Builds the validation agent over the gateway chat model with the read-only code tools. */
    static <T> T agent(Class<T> type, ChatModel model, Path codeDir) {
        return AiServices.builder(type)
                .chatModel(model)
                .tools(new CodeDirectoryReadOnlyTools(codeDir.toString()))
                .build();
    }
}
