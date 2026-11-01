package edu.kit.kastel.mcse.ardoco.sandman.tlr;

import edu.kit.kastel.mcse.ardoco.sandman.agent.KgClient;
import edu.kit.kastel.mcse.ardoco.sandman.agent.RunRecorder;
import edu.kit.kastel.mcse.ardoco.sandman.agent.RunRecorder.RunInfo;
import edu.kit.kastel.mcse.ardoco.sandman.agent.Settings;
import edu.kit.kastel.mcse.ardoco.sandman.agent.a2a.Skill;
import edu.kit.kastel.mcse.ardoco.sandman.agent.a2a.SkillContext;
import edu.kit.kastel.mcse.ardoco.sandman.agent.a2a.SkillException;
import edu.kit.kastel.mcse.ardoco.sandman.agent.a2a.SkillResult;
import jakarta.inject.Inject;
import jakarta.inject.Singleton;
import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeSet;
import org.neo4j.driver.TransactionContext;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ArrayNode;
import tools.jackson.databind.node.ObjectNode;

/** Skill prune-trace-links: retention of derived trace links and own runs (T5.2). */
@Singleton
public class PruneTraceLinks implements Skill {

    static final String SKILL = "prune-trace-links";
    private static final String TOOL = "retention";
    private static final int BATCH = 50;
    private static final int PRUNE_RUN_DAYS = 90;
    private static final List<String> KEYS = List.of("links", "runs");

    private static final class DryRun extends RuntimeException {
        final transient Map<String, Long> removed;

        DryRun(Map<String, Long> removed) {
            super(null, null, false, false);
            this.removed = removed;
        }
    }

    @Inject
    KgClient kg;

    @Inject
    Settings settings;

    @Override
    public String id() {
        return SKILL;
    }

    @Override
    public SkillResult run(SkillContext ctx, JsonNode input) throws SkillException {
        String projectId = input.get("projectId").asString();
        int keep = input.path("keepVersionsPerArtifact").asInt(20);
        boolean dryRun = input.path("dryRun").asBoolean(true);
        Set<String> protectedHashes = new TreeSet<>();
        input.path("protectedContentHashes").forEach(h -> protectedHashes.add(h.asString()));

        long linksBefore = ((Number)
                        kg.read("MATCH (l:TraceLink {projectId: $p}) RETURN count(l) AS n", Map.of("p", projectId))
                                .get(0)
                                .get("n"))
                .longValue();
        List<String> removable = removableVersions(projectId, keep, protectedHashes);
        String cutoff = Instant.now()
                .minus(PRUNE_RUN_DAYS, ChronoUnit.DAYS)
                .truncatedTo(ChronoUnit.SECONDS)
                .toString();
        Map<String, Long> removed = new LinkedHashMap<>();
        KEYS.forEach(k -> removed.put(k, 0L));
        if (dryRun) {
            try {
                kg.writeTx(tx -> {
                    throw new DryRun(prune(tx, projectId, removable, cutoff));
                });
            } catch (DryRun e) {
                removed.putAll(e.removed);
            }
        } else {
            int batches = Math.max(1, (removable.size() + BATCH - 1) / BATCH);
            for (int i = 0; i < batches; i++) {
                List<String> batch = removable.subList(i * BATCH, Math.min(removable.size(), (i + 1) * BATCH));
                Map<String, Long> part = kg.writeTx(tx -> prune(tx, projectId, batch, cutoff));
                part.forEach((k, v) -> removed.merge(k, v, Long::sum));
            }
        }
        long total = removed.values().stream().mapToLong(Long::longValue).sum();

        ObjectNode params = JsonMapper.shared().createObjectNode();
        params.put("keepVersionsPerArtifact", keep);
        params.put("dryRun", dryRun);
        params.set("removed", JsonMapper.shared().valueToTree(removed));
        RunRecorder recorder =
                new RunRecorder(kg, settings.serviceName(), System.getenv().getOrDefault("SANDMAN_VERSION", "dev"));
        String runId = recorder.start(new RunInfo(
                projectId,
                SKILL,
                TOOL,
                "0",
                null,
                null,
                params.toString(),
                null,
                List.of(),
                ctx.workflowId(),
                ctx.taskId(),
                ctx.actor() != null ? ctx.actor() : "service:" + ctx.caller(),
                null));
        recorder.complete(runId);

        ObjectNode output = JsonMapper.shared().createObjectNode();
        output.put("runId", runId);
        output.put("removedCount", total);
        output.put("keptCount", Math.max(0, linksBefore - removed.get("links")));
        output.put("dryRun", dryRun);
        output.set("removed", JsonMapper.shared().valueToTree(removed));
        ObjectNode event = JsonMapper.shared().createObjectNode();
        event.put("projectId", projectId);
        event.put("scope", "trace-links");
        event.put("runId", runId);
        event.put("dryRun", dryRun);
        event.put("keepVersionsPerArtifact", keep);
        event.put("removedCount", total);
        if (!protectedHashes.isEmpty()) {
            ArrayNode hashes = event.putArray("protectedContentHashes");
            protectedHashes.forEach(hashes::add);
        }
        return new SkillResult(output, "sandman.retention.pruned", event);
    }

    private List<String> removableVersions(String projectId, int keep, Set<String> protectedHashes) {
        List<Map<String, Object>> versions = kg.read(
                "MATCH (a:Artifact {projectId: $p})-[:HAS_VERSION]->(v:ArtifactVersion) "
                        + "OPTIONAL MATCH (g:Ingestion)-[:ACTIVATED]->(v) WITH a, v, coalesce(max(g.seq), 0) AS seq "
                        + "OPTIONAL MATCH (a)-[c:CURRENT_VERSION]->(v) "
                        + "RETURN a.id AS art, v.id AS id, v.contentHash AS hash, seq, c IS NOT NULL AS current "
                        + "ORDER BY a.id, seq DESC, v.id",
                Map.of("p", projectId));
        Map<String, Integer> position = new HashMap<>();
        List<String> candidates = new ArrayList<>();
        for (Map<String, Object> v : versions) {
            int n = position.merge((String) v.get("art"), 1, Integer::sum);
            if (n > keep && !Boolean.TRUE.equals(v.get("current")) && !protectedHashes.contains(v.get("hash"))) {
                candidates.add((String) v.get("id"));
            }
        }
        String inVersion = "MATCH (v:ArtifactVersion {projectId: $p})-[:HAS_ELEMENT]->(e:Element) WHERE v.id IN $ids ";
        Set<String> referenced = new HashSet<>();
        kg.read(
                        inVersion
                                + "MATCH (:Inconsistency {projectId: $p})-[:INVOLVES]->(e) RETURN DISTINCT v.id AS id "
                                + "UNION "
                                + inVersion
                                + "MATCH (:Inconsistency {projectId: $p})-[:INVOLVES]->(:TraceLink)-[:SOURCE|TARGET]->(e) "
                                + "RETURN DISTINCT v.id AS id "
                                + "UNION "
                                + inVersion
                                + "MATCH (l:TraceLink {projectId: $p})-[:SOURCE|TARGET]->(e) "
                                + "WHERE l.status <> 'rejected' AND (l.active = true OR l.status = 'confirmed') "
                                + "RETURN DISTINCT v.id AS id",
                        Map.of("p", projectId, "ids", candidates))
                .forEach(r -> referenced.add((String) r.get("id")));
        return candidates.stream().filter(id -> !referenced.contains(id)).toList();
    }

    private long count(TransactionContext tx, String cypher, Map<String, Object> params) {
        return tx.run(cypher, params).list().get(0).get("n").asLong();
    }

    private long delete(TransactionContext tx, String match, Map<String, Object> params) {
        long n = count(tx, match + "RETURN count(l) AS n", params);
        if (n > 0) {
            tx.run(match + "DETACH DELETE l", params).consume();
        }
        return n;
    }

    private Map<String, Long> prune(TransactionContext tx, String projectId, List<String> vids, String cutoff) {
        String agent = settings.serviceName();
        Map<String, Object> p = Map.of("p", projectId, "vids", vids, "explicit", LinkStore.EXTRACTION_OWNED);
        String notReferenced = "OPTIONAL MATCH (:Inconsistency)-[i:INVOLVES]->(l) WITH l, count(i) AS n WHERE n = 0 ";
        String stale = "MATCH (v:ArtifactVersion {projectId: $p})-[:HAS_ELEMENT]->(:Element)<-[:SOURCE|TARGET]-"
                + "(l:TraceLink {projectId: $p}) WHERE v.id IN $vids AND NOT (l.approach IN $explicit) "
                + "AND l.active = false AND coalesce(l.status, '') <> 'confirmed' WITH DISTINCT l "
                + notReferenced;
        String orphans = "MATCH (l:TraceLink {projectId: $p}) WHERE NOT (l.approach IN $explicit) "
                + "OPTIONAL MATCH (l)-[s:SOURCE]->() OPTIONAL MATCH (l)-[t:TARGET]->() "
                + "WITH l, count(s) AS a, count(t) AS b WHERE a = 0 OR b = 0 "
                + notReferenced;
        List<String> runIds = new ArrayList<>();
        for (String match : List.of(stale, orphans)) {
            tx.run(match + "WITH l WHERE l.runId IS NOT NULL RETURN DISTINCT l.runId AS r", p)
                    .list()
                    .forEach(r -> runIds.add(r.get("r").asString()));
        }
        Map<String, Long> removed = new LinkedHashMap<>();
        removed.put("links", delete(tx, stale, p) + delete(tx, orphans, p));
        Map<String, Object> q = Map.of("p", projectId, "agent", agent, "ids", runIds);
        String emptyRuns = "MATCH (l:Run {projectId: $p, agent: $agent}) WHERE l.id IN $ids AND l.status <> 'running' "
                + "OPTIONAL MATCH (l)-[x:PRODUCED]->() WITH l, count(x) AS n WHERE n = 0 ";
        String oldRuns = "MATCH (l:Run {projectId: $p, agent: $agent, skill: $skill}) WHERE l.endedAt < $cutoff ";
        long runs = delete(tx, emptyRuns, q);
        runs += delete(tx, oldRuns, Map.of("p", projectId, "agent", agent, "skill", SKILL, "cutoff", cutoff));
        removed.put("runs", runs);
        return removed;
    }
}
