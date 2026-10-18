package edu.kit.kastel.mcse.ardoco.sandman.tlr;

import edu.kit.kastel.mcse.ardoco.sandman.agent.KgClient;
import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/** KG access for trace links (Stage 1 §4.4): inactive batches, one activating final transaction. */
public final class LinkStore {

    static final int BATCH_SIZE = 5_000;
    /** Links of the extraction-agent (R-006): never superseded, deactivated or pruned by the tlr-agent. */
    static final String EXPLICIT_REFERENCE = "explicit-reference";

    private final KgClient kg;

    private static String now() {
        return Instant.now().truncatedTo(ChronoUnit.SECONDS).toString();
    }

    public LinkStore(KgClient kg) {
        this.kg = kg;
    }

    /** The versions with artifact path, kind and format, ordered by id; versions of other projects are not returned. */
    public List<TlrInput.Version> versions(String projectId, List<String> versionIds) {
        return versionRows(
                "MATCH (a:Artifact)-[:HAS_VERSION]->(v:ArtifactVersion) WHERE v.projectId = $p AND v.id IN $ids ",
                Map.of("p", projectId, "ids", versionIds));
    }

    /** The current version of every artifact of the given kinds, ordered by id. */
    public List<TlrInput.Version> currentVersions(String projectId, java.util.Collection<String> kinds) {
        return versionRows(
                "MATCH (a:Artifact {projectId: $p})-[:CURRENT_VERSION]->(v:ArtifactVersion) WHERE a.kind IN $kinds ",
                Map.of("p", projectId, "kinds", List.copyOf(kinds)));
    }

    private List<TlrInput.Version> versionRows(String match, Map<String, Object> params) {
        return kg
                .read(
                        match + "RETURN v.id AS id, a.id AS artifactId, a.path AS path, a.kind AS kind, "
                                + "a.format AS format, v.storageUri AS storageUri, a.origin AS origin, "
                                + "v.derivedFrom AS derivedFrom ORDER BY id",
                        params)
                .stream()
                .map(r -> new TlrInput.Version(
                        (String) r.get("id"),
                        (String) r.get("artifactId"),
                        (String) r.get("path"),
                        (String) r.get("kind"),
                        (String) r.get("format"),
                        (String) r.get("storageUri"),
                        r.get("origin") == null ? "user" : (String) r.get("origin"),
                        (String) r.get("derivedFrom")))
                .toList();
    }

    /** Elements of the versions with their artifact kind, ordered by key. */
    public List<TlrInput.Element> elements(String projectId, List<String> versionIds) {
        return kg
                .read(
                        "MATCH (a:Artifact)-[:HAS_VERSION]->(v:ArtifactVersion)-[:HAS_ELEMENT]->(e:Element) "
                                + "WHERE v.projectId = $p AND v.id IN $ids "
                                + "RETURN e.id AS id, v.id AS versionId, e.key AS key, labels(e) AS labels, "
                                + "e.kind AS kind, e.name AS name, e.text AS text, e.sentenceIndex AS sentenceIndex, "
                                + "a.kind AS artifactKind ORDER BY key",
                        Map.of("p", projectId, "ids", versionIds))
                .stream()
                .map(r -> new TlrInput.Element(
                        (String) r.get("id"),
                        (String) r.get("versionId"),
                        (String) r.get("key"),
                        ((String) r.get("key")).substring(((String) r.get("key")).indexOf('#') + 1),
                        ((List<?>) r.get("labels"))
                                .stream()
                                        .map(String::valueOf)
                                        .filter(l -> !"Element".equals(l))
                                        .findFirst()
                                        .orElse(null),
                        (String) r.get("kind"),
                        (String) r.get("name"),
                        (String) r.get("text"),
                        r.get("sentenceIndex") == null ? null : ((Number) r.get("sentenceIndex")).intValue(),
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
                "now", now(),
                "explicit", EXPLICIT_REFERENCE);
        kg.writeTx(tx -> tx.run(
                        "MATCH (r:Run {id: $runId}) "
                                + "MATCH (old:TraceLink {projectId: $projectId, approach: $approach, active: true})"
                                + "-[:SOURCE]->(:Element)<-[:HAS_ELEMENT]-(sv:ArtifactVersion), "
                                + "(old)-[:TARGET]->(:Element)<-[:HAS_ELEMENT]-(tv:ArtifactVersion) "
                                + "WHERE sv.id IN $sourceVersionIds AND tv.id IN $targetVersionIds "
                                + "AND NOT (r)-[:PRODUCED]->(old) AND old.approach <> $explicit "
                                + "SET old.active = false "
                                + "WITH count(*) AS superseded "
                                + "MATCH (:Run {id: $runId})-[:PRODUCED]->(x:TraceLink) SET x.active = true "
                                + "WITH count(*) AS activated "
                                + "MATCH (r:Run {id: $runId}) SET r.status = 'completed', r.endedAt = $now",
                        params)
                .consume());
    }

    /** Version ids at the two ends of links that a whole-project run added or retired. */
    public record Changed(Set<String> sources, Set<String> targets) {}

    /**
     * Final transaction of a whole-project run: deactivates the approach's active links between artifacts of the
     * supported kind pairs that this run did not produce, activates this run's links. Returns the version ids at both
     * ends of the retired and of the newly activated links.
     */
    public Changed finishWholeProject(String runId, String projectId, String approach, Set<KindPair> pairs) {
        Map<String, Object> params = Map.of(
                "runId", runId,
                "projectId", projectId,
                "approach", approach,
                "pairs",
                        pairs.stream()
                                .map(p -> p.source() + ">" + p.target())
                                .sorted()
                                .toList(),
                "now", now(),
                "explicit", EXPLICIT_REFERENCE);
        String retired = "MATCH (r:Run {id: $runId}) "
                + "MATCH (old:TraceLink {projectId: $projectId, approach: $approach, active: true})"
                + "-[:SOURCE]->(:Element)<-[:HAS_ELEMENT]-(sv:ArtifactVersion)<-[:HAS_VERSION]-(sa:Artifact), "
                + "(old)-[:TARGET]->(:Element)<-[:HAS_ELEMENT]-(tv:ArtifactVersion)<-[:HAS_VERSION]-(ta:Artifact) "
                + "WHERE sa.kind + '>' + ta.kind IN $pairs "
                + "AND NOT (r)-[:PRODUCED]->(old) AND old.approach <> $explicit ";
        return kg.writeTx(tx -> {
            Set<String> sources = new java.util.TreeSet<>();
            Set<String> targets = new java.util.TreeSet<>();
            for (var row : tx.run(retired + "RETURN DISTINCT sv.id AS s, tv.id AS t", params)
                    .list()) {
                sources.add(row.get("s").asString());
                targets.add(row.get("t").asString());
            }
            for (var row : tx.run(
                            "MATCH (:Run {id: $runId})-[:PRODUCED]->(x:TraceLink) WHERE x.active = false "
                                    + "MATCH (x)-[:SOURCE]->(:Element)<-[:HAS_ELEMENT]-(sv:ArtifactVersion), "
                                    + "(x)-[:TARGET]->(:Element)<-[:HAS_ELEMENT]-(tv:ArtifactVersion) "
                                    + "RETURN DISTINCT sv.id AS s, tv.id AS t",
                            params)
                    .list()) {
                sources.add(row.get("s").asString());
                targets.add(row.get("t").asString());
            }
            tx.run(
                            retired
                                    + "SET old.active = false "
                                    + "WITH count(*) AS superseded "
                                    + "MATCH (:Run {id: $runId})-[:PRODUCED]->(x:TraceLink) SET x.active = true "
                                    + "WITH count(*) AS activated "
                                    + "MATCH (r:Run {id: $runId}) SET r.status = 'completed', r.endedAt = $now",
                            params)
                    .consume();
            return new Changed(sources, targets);
        });
    }

    /** Startup: links of disabled approaches must not stay active. */
    public void deactivate(java.util.Collection<String> approaches) {
        kg.write(
                "MATCH (l:TraceLink {active: true}) WHERE l.approach IN $approaches AND l.approach <> $explicit "
                        + "SET l.active = false",
                Map.of("approaches", List.copyOf(approaches), "explicit", EXPLICIT_REFERENCE));
    }
}
