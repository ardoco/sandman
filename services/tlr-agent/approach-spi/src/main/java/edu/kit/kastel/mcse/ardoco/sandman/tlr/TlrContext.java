package edu.kit.kastel.mcse.ardoco.sandman.tlr;

import edu.kit.kastel.mcse.ardoco.sandman.agent.ArtifactStore;
import edu.kit.kastel.mcse.ardoco.sandman.agent.LlmAccess;
import edu.kit.kastel.mcse.ardoco.sandman.agent.LlmClient;
import java.util.function.BooleanSupplier;
import java.util.function.Consumer;
import org.slf4j.Logger;
import tools.jackson.databind.JsonNode;

/**
 * Per-run services for an approach; llm only for approaches with usesLlm(), artifacts null if no store is configured.
 * llmClient is null in environment() and for approaches without usesLlm(); it is one client per Run. progress takes a
 * short text of the current activity (the skill throttles it to one update per 2 s); it never fails.
 */
public record TlrContext(
        LlmAccess llm,
        ArtifactStore artifacts,
        BooleanSupplier cancelled,
        Logger log,
        JsonNode params,
        LlmClient llmClient,
        Consumer<String> progress) {

    public TlrContext(
            LlmAccess llm,
            ArtifactStore artifacts,
            BooleanSupplier cancelled,
            Logger log,
            JsonNode params,
            LlmClient llmClient) {
        this(llm, artifacts, cancelled, log, params, llmClient, text -> {});
    }

    public TlrContext(LlmAccess llm, ArtifactStore artifacts, BooleanSupplier cancelled, Logger log, JsonNode params) {
        this(llm, artifacts, cancelled, log, params, null);
    }
}
