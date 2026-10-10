package edu.kit.kastel.mcse.ardoco.sandman.tlr;

import java.util.List;

/** Elements of the given source and target versions, each ordered by key. */
public record TlrInput(String projectId, List<Element> sources, List<Element> targets) {

    /** label = kind label (Requirement, CodeUnit, ...); artifactKind = kind of the owning artifact. */
    public record Element(
            String id, String key, String label, String kind, String name, String text, String artifactKind) {}
}
