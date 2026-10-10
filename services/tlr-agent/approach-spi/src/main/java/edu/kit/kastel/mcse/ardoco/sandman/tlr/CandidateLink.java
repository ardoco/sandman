package edu.kit.kastel.mcse.ardoco.sandman.tlr;

/** A link proposed by an approach; rationale may be null. */
public record CandidateLink(String sourceId, String targetId, String linkType, double confidence, String rationale) {}
