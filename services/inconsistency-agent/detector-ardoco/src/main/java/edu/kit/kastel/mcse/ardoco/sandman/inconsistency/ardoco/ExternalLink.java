package edu.kit.kastel.mcse.ardoco.sandman.inconsistency.ardoco;

/** A sentence-level trace link as ArDoCo sees it: 0-based line (sentence) index and ArDoCo model id. */
record ExternalLink(int sentence, String modelId) {}
