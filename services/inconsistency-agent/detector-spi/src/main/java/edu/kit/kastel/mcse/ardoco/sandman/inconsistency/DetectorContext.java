package edu.kit.kastel.mcse.ardoco.sandman.inconsistency;

import edu.kit.kastel.mcse.ardoco.sandman.agent.ArtifactStore;
import edu.kit.kastel.mcse.ardoco.sandman.agent.KgClient;
import java.util.Map;
import java.util.function.BooleanSupplier;

/**
 * What a detector may use: read access to the KG, the project's LLM policy, its own params from plugins.yaml,
 * the artifact store (null if not configured) and the cancellation flag.
 */
public record DetectorContext(
        KgClient kg,
        String llmPolicy,
        String queryServiceUrl,
        Map<String, Object> params,
        ArtifactStore artifacts,
        BooleanSupplier cancelled) {

    public DetectorContext(KgClient kg, String llmPolicy, String queryServiceUrl) {
        this(kg, llmPolicy, queryServiceUrl, Map.of(), null, () -> false);
    }

    public DetectorContext forDetector(Map<String, Object> detectorParams) {
        return new DetectorContext(kg, llmPolicy, queryServiceUrl, detectorParams, artifacts, cancelled);
    }
}
