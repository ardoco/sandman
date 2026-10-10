package edu.kit.kastel.mcse.ardoco.sandman.tlr;

import java.util.List;

/** Versions with their elements; sources and targets are each ordered by key. */
public record TlrInput(String projectId, List<Version> versions, List<Element> sources, List<Element> targets) {

    /** An artifact version; format may be null, storageUri is artifact://project/sha256. */
    public record Version(String id, String artifactId, String path, String kind, String format, String storageUri) {}

    /**
     * label = kind label (Requirement, CodeUnit, ...); artifactKind = kind of the owning artifact; localKey = key
     * without the artifact id; sentenceIndex = 0-based position of a TextUnit sentence, else null.
     */
    public record Element(
            String id,
            String versionId,
            String key,
            String localKey,
            String label,
            String kind,
            String name,
            String text,
            Integer sentenceIndex,
            String artifactKind) {}
}
