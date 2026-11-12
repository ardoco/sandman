package edu.kit.kastel.mcse.ardoco.sandman.inconsistency.ardoco;

import dev.langchain4j.model.chat.ChatModel;
import edu.kit.kastel.mcse.ardoco.id.ArchitecturalStylePatternNerStrategy;
import edu.kit.kastel.mcse.ardoco.id.informants.llmagents.ArchitecturalStylePatternValidationLlmAgent;
import edu.kit.kastel.mcse.ardoco.sandman.agent.LlmAccess;
import edu.kit.kastel.mcse.ardoco.tlr.artemis.strategies.ArtemisNerStrategy;
import java.nio.file.Path;
import java.util.function.Function;

/** Statements about architectural styles and patterns in the text, checked against the code files (T3.9). */
public final class ArchitecturalStyleIdDetector extends CodeStatementDetector {

    public static final String ID = "architectural-style-id";
    public static final String TYPE = "architectural-style-mismatch";
    /** Raise the first part when the mapping of Artemis results to findings changes. */
    public static final String VERSION = "0.1.0+21ef682";

    public ArchitecturalStyleIdDetector() {
        this(envAccess());
    }

    public ArchitecturalStyleIdDetector(Function<String, LlmAccess> access) {
        super(
                access,
                ID,
                VERSION,
                TYPE,
                "architectural style or pattern",
                "ArDoCo architectural style inconsistency detection (LLM): statements about architectural styles and patterns that the code contradicts");
    }

    @Override
    ArtemisNerStrategy strategy() {
        return new ArchitecturalStylePatternNerStrategy();
    }

    @Override
    Validator validator(ChatModel model, Path codeDir) {
        var agent = agent(ArchitecturalStylePatternValidationLlmAgent.class, model, codeDir);
        return (occurrence, name, fullText, line, path) -> {
            var r = agent.validateArchitecturalStylePatternStatementAgainstCodeDirectory(
                    occurrence, name, fullText, line, path);
            return new Verdict(r.valid(), r.explanation());
        };
    }
}
