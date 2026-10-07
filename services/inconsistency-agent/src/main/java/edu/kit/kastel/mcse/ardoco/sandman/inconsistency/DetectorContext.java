package edu.kit.kastel.mcse.ardoco.sandman.inconsistency;

import edu.kit.kastel.mcse.ardoco.sandman.agent.KgClient;

/** What a detector may use: read access to the KG and the project's LLM policy. */
public record DetectorContext(KgClient kg, String llmPolicy, String queryServiceUrl) {}
