package edu.kit.kastel.mcse.ardoco.sandman.ardocotext;

/** The CoreNLP service is down or did not answer after the client's retries. */
public class CorenlpUnavailableException extends RuntimeException {

    public CorenlpUnavailableException(String message, Throwable cause) {
        super(message, cause);
    }
}
