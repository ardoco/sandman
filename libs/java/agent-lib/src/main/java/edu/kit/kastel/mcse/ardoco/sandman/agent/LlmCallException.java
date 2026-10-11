package edu.kit.kastel.mcse.ardoco.sandman.agent;

/** The gateway or llm-replay rejected a call (4xx other than 402 and 429, for example a replay miss). Not retryable. */
public class LlmCallException extends RuntimeException {

    public LlmCallException(String message) {
        super(message);
    }
}
