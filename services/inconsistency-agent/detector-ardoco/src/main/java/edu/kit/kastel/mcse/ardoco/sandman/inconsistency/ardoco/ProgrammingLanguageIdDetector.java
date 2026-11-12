package edu.kit.kastel.mcse.ardoco.sandman.inconsistency.ardoco;

import dev.langchain4j.model.chat.ChatModel;
import edu.kit.kastel.mcse.ardoco.id.ProgrammingLanguageNerStrategy;
import edu.kit.kastel.mcse.ardoco.id.informants.llmagents.ProgrammingLanguageValidationLlmAgent;
import edu.kit.kastel.mcse.ardoco.sandman.agent.LlmAccess;
import edu.kit.kastel.mcse.ardoco.tlr.artemis.strategies.ArtemisNerStrategy;
import java.nio.file.Path;
import java.util.function.Function;

/** Statements about programming languages in the text, checked against the code files (T3.9). */
public final class ProgrammingLanguageIdDetector extends CodeStatementDetector {

    public static final String ID = "programming-language-id";
    public static final String TYPE = "programming-language-mismatch";
    /** Raise the first part when the mapping of Artemis results to findings changes. */
    public static final String VERSION = "0.1.0+21ef682";

    public ProgrammingLanguageIdDetector() {
        this(envAccess());
    }

    public ProgrammingLanguageIdDetector(Function<String, LlmAccess> access) {
        super(
                access,
                ID,
                VERSION,
                TYPE,
                "programming language",
                "ArDoCo programming language inconsistency detection (LLM): statements about programming languages that the code contradicts");
    }

    @Override
    ArtemisNerStrategy strategy() {
        return new ProgrammingLanguageNerStrategy();
    }

    @Override
    Validator validator(ChatModel model, Path codeDir) {
        var agent = agent(ProgrammingLanguageValidationLlmAgent.class, model, codeDir);
        return (occurrence, name, fullText, line, path) -> {
            var r = agent.validateProgrammingLanguageStatementAgainstCodeDirectory(
                    occurrence, name, fullText, line, path);
            return new Verdict(r.valid(), r.explanation());
        };
    }
}
