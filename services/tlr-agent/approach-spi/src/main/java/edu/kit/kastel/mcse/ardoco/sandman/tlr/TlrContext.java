package edu.kit.kastel.mcse.ardoco.sandman.tlr;

import edu.kit.kastel.mcse.ardoco.sandman.agent.ArtifactStore;
import edu.kit.kastel.mcse.ardoco.sandman.agent.LlmAccess;
import java.util.function.BooleanSupplier;
import org.slf4j.Logger;
import tools.jackson.databind.JsonNode;

/** Per-run services for an approach; llm only for approaches with usesLlm(), artifacts null if no store is configured. */
public record TlrContext(
        LlmAccess llm, ArtifactStore artifacts, BooleanSupplier cancelled, Logger log, JsonNode params) {}
