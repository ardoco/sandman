package edu.kit.kastel.mcse.ardoco.sandman.agent;

/** The gateway did not answer after its retries (HTTP 429, 5xx or connect errors). The owner may retry later. */
public class LlmUnavailableException extends RuntimeException {

    public LlmUnavailableException(String message, Throwable cause) {
        super(message, cause);
    }
}
