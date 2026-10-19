package edu.kit.kastel.mcse.ardoco.sandman.inconsistency.ardoco;

import edu.kit.kastel.mcse.ardoco.sandman.agent.KgClient;
import java.util.List;
import java.util.Map;

/** KG reads of the detector: current versions, sentence TextUnits, model elements and active links. */
final class KgInput {

    record Version(String id, String path, String format, String storageUri) {}

    record Sentence(String id, String key, String text) {
        String localKey() {
            return key.substring(key.indexOf('#') + 1);
        }
    }

    record ModelElement(String id, String key, String name, boolean inferred) {
        String localKey() {
            return key.substring(key.indexOf('#') + 1);
        }
    }

    record LinkPair(String sentenceId, String modelElementId) {}

    private final KgClient kg;

    KgInput(KgClient kg) {
        this.kg = kg;
    }

    List<Version> currentVersions(String projectId, List<String> kinds) {
        return kg
                .read(
                        "MATCH (a:Artifact {projectId: $p})-[:CURRENT_VERSION]->(v:ArtifactVersion) WHERE a.kind IN $kinds "
                                + "RETURN v.id AS id, a.path AS path, a.format AS format, v.storageUri AS storageUri ORDER BY id",
                        Map.of("p", projectId, "kinds", kinds))
                .stream()
                .map(r -> new Version((String) r.get("id"), (String) r.get("path"), (String) r.get("format"), (String)
                        r.get("storageUri")))
                .toList();
    }

    /** Sentence TextUnits ordered by sentenceIndex. */
    List<Sentence> sentences(String versionId) {
        return kg
                .read(
                        "MATCH (:ArtifactVersion {id: $v})-[:HAS_ELEMENT]->(e:TextUnit {kind: 'sentence'}) "
                                + "WHERE e.sentenceIndex IS NOT NULL "
                                + "RETURN e.id AS id, e.key AS key, e.text AS text ORDER BY e.sentenceIndex",
                        Map.of("v", versionId))
                .stream()
                .map(r -> new Sentence((String) r.get("id"), (String) r.get("key"), (String) r.get("text")))
                .toList();
    }

    List<ModelElement> modelElements(String versionId) {
        return kg
                .read(
                        "MATCH (:ArtifactVersion {id: $v})-[:HAS_ELEMENT]->(e:ModelElement) "
                                + "RETURN e.id AS id, e.key AS key, e.name AS name, e.inferred AS inferred ORDER BY key",
                        Map.of("v", versionId))
                .stream()
                .map(r -> new ModelElement(
                        (String) r.get("id"),
                        (String) r.get("key"),
                        (String) r.get("name"),
                        Boolean.TRUE.equals(r.get("inferred"))))
                .toList();
    }

    /** Active, non-rejected links between a sentence of the doc version and an element of the model version. */
    List<LinkPair> links(String docVersionId, String modelVersionId, List<String> approaches) {
        return kg
                .read(
                        "MATCH (l:TraceLink {active: true})-[:SOURCE|TARGET]->(s:TextUnit)<-[:HAS_ELEMENT]-(:ArtifactVersion {id: $d}), "
                                + "(l)-[:SOURCE|TARGET]->(m:ModelElement)<-[:HAS_ELEMENT]-(:ArtifactVersion {id: $m}) "
                                + "WHERE l.status <> 'rejected' AND ($all OR l.approach IN $approaches) "
                                + "RETURN DISTINCT s.id AS sentenceId, m.id AS modelElementId ORDER BY sentenceId, modelElementId",
                        Map.of(
                                "d",
                                docVersionId,
                                "m",
                                modelVersionId,
                                "all",
                                approaches == null,
                                "approaches",
                                approaches == null ? List.of() : approaches))
                .stream()
                .map(r -> new LinkPair((String) r.get("sentenceId"), (String) r.get("modelElementId")))
                .toList();
    }
}
