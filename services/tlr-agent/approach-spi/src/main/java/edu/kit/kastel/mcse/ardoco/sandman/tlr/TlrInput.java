package edu.kit.kastel.mcse.ardoco.sandman.tlr;

import java.util.List;

/** Versions with their elements; sources and targets are each ordered by key. */
public record TlrInput(String projectId, List<Version> versions, List<Element> sources, List<Element> targets) {

    /**
     * An artifact version; format may be null, storageUri is artifact://project/sha256. origin is "user" or "derived";
     * derivedFrom is the source document version id of a derived version, else null.
     */
    public record Version(
            String id,
            String artifactId,
            String path,
            String kind,
            String format,
            String storageUri,
            String origin,
            String derivedFrom) {

        public Version(String id, String artifactId, String path, String kind, String format, String storageUri) {
            this(id, artifactId, path, kind, format, storageUri, "user", null);
        }

        public boolean derived() {
            return "derived".equals(origin);
        }

        /** A user model pairs with every document; a derived model only with the document version it was derived from. */
        public boolean pairsWithDoc(String docVersionId) {
            return !derived() || docVersionId.equals(derivedFrom);
        }
    }

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
