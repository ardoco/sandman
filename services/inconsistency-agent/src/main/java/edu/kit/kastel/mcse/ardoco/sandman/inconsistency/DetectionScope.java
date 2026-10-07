package edu.kit.kastel.mcse.ardoco.sandman.inconsistency;

import java.util.Set;

/** Artifacts to analyse; wholeProject = the request had no scopeArtifactIds (artifactIds then lists all current ones). */
public record DetectionScope(String projectId, Set<String> artifactIds, boolean wholeProject) {}
