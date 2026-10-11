package edu.kit.kastel.mcse.ardoco.sandman.tlr;

/**
 * An external dependency of the approach is down after the approach's own retries. The skill finishes the other
 * approaches, records this approach's Run as failed, and then fails with DEPENDENCY_UNAVAILABLE (retryable).
 */
public class ApproachUnavailableException extends RuntimeException {

    public ApproachUnavailableException(String message, Throwable cause) {
        super(message, cause);
    }
}
