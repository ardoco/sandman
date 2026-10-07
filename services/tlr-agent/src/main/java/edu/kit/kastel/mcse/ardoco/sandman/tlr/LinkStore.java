package edu.kit.kastel.mcse.ardoco.sandman.tlr;

import edu.kit.kastel.mcse.ardoco.sandman.agent.KgClient;
import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/** KG access for trace links (Stage 1 §4.4): inactive batches, one activating final transaction. */
public final class LinkStore {

    static final int BATCH_SIZE = 5_000;

    private final KgClient kg;

    private static String now() {
        return Instant.now().truncatedTo(ChronoUnit.SECONDS).toString();
    }

    public LinkStore(KgClient kg) {
        this.kg = kg;
    }

    /** Elements of the versions with their artifact kind, ordered by key. */
    public List<TlrInput.Element> elements(String projectId, List<String> versionIds) {
        return kg
                .read(
                        "MATCH (a:Artifact)-[:HAS_VERSION]->(v:ArtifactVersion)-[:HAS_ELEMENT]->(e:Element) "
                                + "WHERE v.projectId = $p AND v.id IN $ids "
                                + "RETURN e.id AS id, e.key AS key, labels(e) AS labels, e.kind AS kind, "
                                + "e.name AS name, e.text AS text, a.kind AS artifactKind ORDER BY key",
                        Map.of("p", projectId, "ids", versionIds))
                .stream()
                .map(r -> new TlrInput.Element(
                        (String) r.get("id"),
                        (String) r.get("key"),
                        ((List<?>) r.get("labels"))
                                .stream()
                                        .map(String::valueOf)
                                        .filter(l -> !"Element".equals(l))
                                        .findFirst()
                                        .orElse(null),
                        (String) r.get("kind"),
                        (String) r.get("name"),
                        (String) r.get("text"),
                        (String) r.get("artifactKind")))
                .toList();
    }

    /** Writes the links inactive in batches of {@value #BATCH_SIZE}; links with existing ids are kept as they are. */
    public void writeInactive(
            String runId,
            String projectId,
            TraceLinkApproach approach,
            String configHash,
            List<Map<String, Object>> links) {
        String now = now();
        for (int i = 0; i < links.size(); i += BATCH_SIZE) {
            Map<String, Object> params = new HashMap<>();
            params.put("runId", runId);
            params.put("projectId", projectId);
            params.put("approach", approach.id());
            params.put("approachVersion", approach.version());
            params.put("configHash", configHash);
            params.put("now", now);
            params.put("links", new ArrayList<>(links.subList(i, Math.min(i + BATCH_SIZE, links.size()))));
            kg.write(
                    "MATCH (r:Run {id: $runId}) UNWIND $links AS l "
                            + "MATCH (s:Element {id: l.sourceId}), (t:Element {id: l.targetId}) "
                            + "MERGE (x:TraceLink {id: l.id}) "
                            + "ON CREATE SET x.projectId = $projectId, x.linkType = l.linkType, x.confidence = l.confidence, "
                            + "x.approach = $approach, x.approachVersion = $approachVersion, x.configHash = $configHash, "
                            + "x.status = 'candidate', x.active = false, x.runId = $runId, x.createdAt = $now, "
                            + "x.rationale = l.rationale "
                            + "MERGE (x)-[:SOURCE]->(s) MERGE (x)-[:TARGET]->(t) MERGE (r)-[:PRODUCED]->(x)",
                    params);
        }
    }

    /** Final transaction: supersedes the approach's earlier active links between the versions, activates this run's links. */
    public void finish(
            String runId,
            String projectId,
            String approach,
            List<String> sourceVersionIds,
            List<String> targetVersionIds) {
        Map<String, Object> params = Map.of(
                "runId", runId,
                "projectId", projectId,
                "approach", approach,
                "sourceVersionIds", sourceVersionIds,
                "targetVersionIds", targetVersionIds,
                "now", now());
        kg.writeTx(tx -> tx.run(
                        "MATCH (r:Run {id: $runId}) "
                                + "MATCH (old:TraceLink {projectId: $projectId, approach: $approach, active: true})"
                                + "-[:SOURCE]->(:Element)<-[:HAS_ELEMENT]-(sv:ArtifactVersion), "
                                + "(old)-[:TARGET]->(:Element)<-[:HAS_ELEMENT]-(tv:ArtifactVersion) "
                                + "WHERE sv.id IN $sourceVersionIds AND tv.id IN $targetVersionIds "
                                + "AND NOT (r)-[:PRODUCED]->(old) "
                                + "SET old.active = false "
                                + "WITH count(*) AS superseded "
                                + "MATCH (:Run {id: $runId})-[:PRODUCED]->(x:TraceLink) SET x.active = true "
                                + "WITH count(*) AS activated "
                                + "MATCH (r:Run {id: $runId}) SET r.status = 'completed', r.endedAt = $now",
                        params)
                .consume());
    }

    /** Startup: links of disabled approaches must not stay active. */
    public void deactivate(java.util.Collection<String> approaches) {
        kg.write(
                "MATCH (l:TraceLink {active: true}) WHERE l.approach IN $approaches SET l.active = false",
                Map.of("approaches", List.copyOf(approaches)));
    }
}
