package edu.kit.kastel.mcse.ardoco.sandman.agent.a2a;

/** Skill failure reported as SkillError (common.schema.json): code, message, retryable. */
public class SkillException extends Exception {

    private final String code;
    private final boolean retryable;

    public SkillException(String code, String message, boolean retryable) {
        super(message);
        this.code = code;
        this.retryable = retryable;
    }

    public String code() {
        return code;
    }

    public boolean retryable() {
        return retryable;
    }

    @Override
    public String toString() {
        return code + ": " + getMessage();
    }
}
