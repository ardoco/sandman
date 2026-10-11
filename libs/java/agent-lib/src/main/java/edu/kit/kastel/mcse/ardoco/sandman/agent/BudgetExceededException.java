package edu.kit.kastel.mcse.ardoco.sandman.agent;

/** The gateway budget of the virtual key is exhausted (HTTP 402). Not retryable. */
public class BudgetExceededException extends RuntimeException {

    public BudgetExceededException(String message) {
        super(message);
    }
}
