package edu.kit.kastel.mcse.ardoco.sandman.inconsistency;

import edu.kit.kastel.mcse.ardoco.sandman.agent.Contracts;
import edu.kit.kastel.mcse.ardoco.sandman.agent.Ids;
import edu.kit.kastel.mcse.ardoco.sandman.agent.Jcs;
import edu.kit.kastel.mcse.ardoco.sandman.agent.KgClient;
import edu.kit.kastel.mcse.ardoco.sandman.agent.RunRecorder;
import edu.kit.kastel.mcse.ardoco.sandman.agent.a2a.SkillException;
import edu.kit.kastel.mcse.ardoco.sandman.inconsistency.InconsistencyStore.Item;
import edu.kit.kastel.mcse.ardoco.sandman.inconsistency.Lifecycle.Status;
import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;
import java.util.TreeSet;
import java.util.function.Consumer;
import java.util.stream.Collectors;
import org.neo4j.driver.TransactionContext;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ArrayNode;
import tools.jackson.databind.node.ObjectNode;

/** One detection run: detectors, fingerprints and the re-detection algorithm of Stage 1 §4.3, written in one transaction. */
public final class DetectionService {

    private static final String FINDING = "skills.schema.json#/$defs/Finding";
    private static final JsonMapper JSON = JsonMapper.shared();

    /** skill input plus the A2A task context. */
    public record Request(
            String projectId,
            List<String> scopeArtifactIds,
            List<String> detectors,
            String workflowId,
            String taskId,
            String actor) {}

    /** Skill output and the data of sandman.inconsistencies.updated. */
    public record Result(ObjectNode output, ObjectNode eventData) {}

    /** A validated finding with identity and resolved graph nodes. */
    private record Prepared(
            String projectId,
            Finding finding,
            InconsistencyDetector detector,
            String id,
            String fingerprint,
            List<Edge> edges,
            String evidenceHash) {}

    /** INVOLVES edge target: Element id or TraceLink id. */
    private record Edge(boolean link, String nodeId, String role) {}

    private record Written(List<String> created, List<String> reopened, List<String> resolved, long stillOpen) {}

    private final KgClient kg;
    private final Contracts contracts;
    private final RunRecorder runs;
    private final DetectorRegistry registry;
    private final String queryServiceUrl;
    private final Consumer<ObjectNode> notifications;

    public DetectionService(
            KgClient kg,
            Contracts contracts,
            RunRecorder runs,
            DetectorRegistry registry,
            String queryServiceUrl,
            Consumer<ObjectNode> notifications) {
        this.kg = kg;
        this.contracts = contracts;
        this.runs = runs;
        this.registry = registry;
        this.queryServiceUrl = queryServiceUrl;
        this.notifications = notifications;
    }

    public Result detect(Request req) throws SkillException {
        String projectId = req.projectId();
        var project = kg.read("MATCH (p:Project {id: $id}) RETURN p.llmPolicy AS policy", Map.of("id", projectId));
        if (project.isEmpty()) {
            throw new SkillException("INVALID_INPUT", "unknown project " + projectId, false);
        }
        List<InconsistencyDetector> selected = select(req.detectors());
        Map<String, String> currentVersions = currentVersions(projectId);
        boolean whole = req.scopeArtifactIds() == null;
        Set<String> scope = whole ? currentVersions.keySet() : new TreeSet<>(req.scopeArtifactIds());
        // snapshot before the detectors run: system transitions are conditional on it, a user decision wins
        List<Item> existing = InconsistencyStore.loadAll(kg, projectId);

        String runId = runs.start(runInfo(req, selected, scope, whole, currentVersions));
        try {
            var ctx = new DetectorContext(kg, (String) project.get(0).get("policy"), queryServiceUrl);
            var ds = new DetectionScope(projectId, scope, whole);
            Map<String, Prepared> detected = new LinkedHashMap<>();
            Set<String> completed = new HashSet<>();
            ArrayNode detectorRuns = JSON.createArrayNode();
            for (InconsistencyDetector d : selected) {
                ObjectNode entry = detectorRuns.addObject().put("detector", d.id());
                try {
                    var detection = d.detectWithWarnings(ds, ctx);
                    List<Finding> findings = detection.findings();
                    prepare(projectId, d, findings).forEach(p -> detected.putIfAbsent(p.id(), p));
                    completed.add(d.id());
                    entry.put("status", "completed").put("findingCount", findings.size());
                    if (!detection.warnings().isEmpty()) {
                        detection.warnings().forEach(entry.putArray("warnings")::add);
                    }
                } catch (RuntimeException e) { // a failing detector or plugin only makes the run partial
                    entry.put("status", "failed").put("findingCount", 0).put("error", String.valueOf(e.getMessage()));
                }
            }
            boolean partial = completed.size() < selected.size();
            String now = Instant.now().truncatedTo(ChronoUnit.SECONDS).toString();
            Written w = kg.writeTx(tx ->
                    write(tx, projectId, runId, now, existing, detected, completed, scope, currentVersions.keySet()));
            runs.complete(runId, partial ? "partial" : "completed", null, null);
            w.created().forEach(id -> notify(detected.get(id), id, "OPEN"));
            w.reopened().forEach(id -> notify(detected.get(id), id, "REOPENED"));
            return result(req, runId, partial, whole, scope, w, detectorRuns);
        } catch (RuntimeException e) {
            runs.fail(runId, String.valueOf(e.getMessage()));
            throw e;
        }
    }

    private List<InconsistencyDetector> select(List<String> ids) throws SkillException {
        if (ids == null) {
            return registry.all();
        }
        List<InconsistencyDetector> selected = new ArrayList<>();
        for (String id : ids) {
            InconsistencyDetector d = registry.get(id);
            if (d == null) {
                throw new SkillException("INVALID_INPUT", "unknown or disabled detector " + id, false);
            }
            selected.add(d);
        }
        return selected;
    }

    private Map<String, String> currentVersions(String projectId) {
        Map<String, String> versions = new TreeMap<>();
        kg.read(
                        "MATCH (:Project {id: $p})-[:HAS_ARTIFACT]->(a:Artifact)-[:CURRENT_VERSION]->(v:ArtifactVersion) "
                                + "RETURN a.id AS artifact, v.id AS version",
                        Map.of("p", projectId))
                .forEach(row -> versions.put((String) row.get("artifact"), (String) row.get("version")));
        return versions;
    }

    private RunRecorder.RunInfo runInfo(
            Request req,
            List<InconsistencyDetector> selected,
            Set<String> scope,
            boolean whole,
            Map<String, String> currentVersions) {
        ObjectNode key = JSON.createObjectNode().put("projectId", req.projectId());
        if (!whole) {
            key.putArray("scope")
                    .addAll(scope.stream()
                            .map(JSON.getNodeFactory()::stringNode)
                            .toList());
        }
        ArrayNode tools = key.putArray("detectors");
        selected.forEach(d -> tools.add(d.id() + "@" + d.version()));
        return new RunRecorder.RunInfo(
                req.projectId(),
                "detect-inconsistencies",
                selected.stream().map(InconsistencyDetector::id).collect(Collectors.joining(",")),
                selected.stream().map(InconsistencyDetector::version).collect(Collectors.joining(",")),
                Ids.sha256Hex(Jcs.canonicalize(key)),
                null,
                null,
                null,
                scope.stream().map(currentVersions::get).filter(v -> v != null).toList(),
                req.workflowId(),
                req.taskId(),
                req.actor(),
                null);
    }

    /** Validates findings and resolves their involved elements and links; throws on unknown references. */
    private List<Prepared> prepare(String projectId, InconsistencyDetector d, List<Finding> findings) {
        findings.forEach(f -> contracts.validate(FINDING, f.toJson()));
        Set<String> linkIds = new TreeSet<>();
        Set<String> keys = new TreeSet<>();
        for (Finding f : findings) {
            for (Finding.Involved i : f.involved()) {
                if (i.elementKey() != null) {
                    keys.add(i.elementKey());
                } else {
                    linkIds.add(i.traceLinkId());
                }
            }
        }
        Map<String, String[]> links = new HashMap<>();
        kg.read(
                        "MATCH (l:TraceLink {projectId: $p}) WHERE l.id IN $ids "
                                + "OPTIONAL MATCH (l)-[:SOURCE]->(s:Element) OPTIONAL MATCH (l)-[:TARGET]->(t:Element) "
                                + "RETURN l.id AS id, s.key AS sk, t.key AS tk",
                        Map.of("p", projectId, "ids", List.copyOf(linkIds)))
                .forEach(row -> {
                    if (row.get("sk") != null && row.get("tk") != null) {
                        links.put(
                                (String) row.get("id"), new String[] {(String) row.get("sk"), (String) row.get("tk")});
                        keys.add((String) row.get("sk"));
                        keys.add((String) row.get("tk"));
                    }
                });
        Map<String, Map<String, Object>> elements = new HashMap<>();
        kg.read(
                        "MATCH (e:Element) WHERE e.key IN $keys AND e.projectId = $p "
                                + "MATCH (e)<-[:HAS_ELEMENT]-(:ArtifactVersion)<-[:CURRENT_VERSION]-(:Artifact) "
                                + "RETURN e.key AS key, e.id AS id, e.contentHash AS hash",
                        Map.of("p", projectId, "keys", List.copyOf(keys)))
                .forEach(row -> elements.put((String) row.get("key"), row));

        Map<String, Prepared> byId = new LinkedHashMap<>();
        for (Finding f : findings) {
            List<String> involvedKeys = new ArrayList<>();
            List<String> hashPairs = new ArrayList<>();
            List<Edge> edges = new ArrayList<>();
            for (Finding.Involved i : f.involved()) {
                if (i.elementKey() != null) {
                    var e = elements.get(i.elementKey());
                    if (e == null) {
                        throw new IllegalArgumentException("unknown or non-current element " + i.elementKey());
                    }
                    involvedKeys.add(i.elementKey());
                    hashPairs.add(pair(i.elementKey(), e.get("hash")));
                    edges.add(new Edge(false, (String) e.get("id"), i.role()));
                } else {
                    String[] ends = links.get(i.traceLinkId());
                    if (ends == null) {
                        throw new IllegalArgumentException("unknown trace link " + i.traceLinkId());
                    }
                    involvedKeys.add(ends[0] + "->" + ends[1]);
                    for (String end : ends) {
                        var e = elements.get(end);
                        if (e == null) {
                            throw new IllegalArgumentException("unknown or non-current element " + end);
                        }
                        hashPairs.add(pair(end, e.get("hash")));
                    }
                    edges.add(new Edge(true, i.traceLinkId(), i.role()));
                }
            }
            String fingerprint = Ids.fingerprint(f.type(), d.id(), involvedKeys);
            byId.putIfAbsent(
                    Ids.stableId("inc", projectId, fingerprint),
                    new Prepared(
                            projectId,
                            f,
                            d,
                            Ids.stableId("inc", projectId, fingerprint),
                            fingerprint,
                            edges,
                            Ids.evidenceHash(hashPairs)));
        }
        return List.copyOf(byId.values());
    }

    private static String pair(String key, Object hash) {
        return key + "=" + hash;
    }

    /** Re-detection steps 2 to 5; every system write is conditional on the observed status. */
    private Written write(
            TransactionContext tx,
            String projectId,
            String runId,
            String now,
            List<Item> existing,
            Map<String, Prepared> detected,
            Set<String> completed,
            Set<String> scope,
            Set<String> currentArtifacts) {
        Map<String, Item> byId = existing.stream().collect(Collectors.toMap(Item::id, i -> i));
        List<Map<String, Object>> create = new ArrayList<>();
        List<Map<String, Object>> refresh = new ArrayList<>();
        List<Map<String, Object>> reopen = new ArrayList<>();
        List<Map<String, Object>> touch = new ArrayList<>();
        List<Map<String, Object>> resolve = new ArrayList<>();

        Set<String> deleted = new HashSet<>();
        for (Item item : existing) { // step 4: independent of scope and detectors
            if (Lifecycle.autoResolvable(item.status(), item.verification())
                    && !currentArtifacts.containsAll(item.artifactIds())) {
                deleted.add(item.id());
                resolve.add(resolve(item, "artifact-deleted"));
            }
        }
        for (Prepared p : detected.values()) { // step 2
            Item item = byId.get(p.id());
            if (item == null) {
                create.add(item(p, null, runId, now));
            } else if (!deleted.contains(p.id())) {
                switch (item.status()) {
                    case OPEN, REOPENED, DEFERRED -> refresh.add(item(p, item, runId, now));
                    case RESOLVED -> reopen.add(item(p, item, runId, now));
                    case ACCEPTED, REJECTED ->
                        (p.evidenceHash().equals(item.props().get("acceptedEvidenceHash")) ? touch : reopen)
                                .add(item(p, item, runId, now));
                }
            }
        }
        for (Item item : existing) { // step 3
            if (Lifecycle.autoResolvable(item.status(), item.verification())
                    && !deleted.contains(item.id())
                    && !detected.containsKey(item.id())
                    && completed.contains(item.props().get("detector"))
                    && scope.containsAll(item.artifactIds())) {
                resolve.add(resolve(item, "not-redetected"));
            }
        }

        Map<String, Object> params = new HashMap<>();
        params.put("runId", runId);
        params.put("now", now);
        List<String> created = run(tx, CREATE, params, create);
        List<String> refreshed = run(tx, REFRESH, params, refresh);
        List<String> reopened = run(tx, REOPEN, params, reopen);
        run(tx, TOUCH, params, touch);
        List<String> resolved = run(tx, RESOLVE, params, resolve);

        List<String> relink = new ArrayList<>(created);
        relink.addAll(refreshed);
        relink.addAll(reopened);
        if (!refreshed.isEmpty() || !reopened.isEmpty()) {
            List<String> old = new ArrayList<>(refreshed);
            old.addAll(reopened);
            tx.run("UNWIND $ids AS id MATCH (:Inconsistency {id: id})-[r:INVOLVES]->() DELETE r", Map.of("ids", old))
                    .consume();
        }
        List<Map<String, Object>> elementEdges = new ArrayList<>();
        List<Map<String, Object>> linkEdges = new ArrayList<>();
        for (String id : relink) {
            Prepared p = detected.get(id);
            for (Edge e : p.edges()) {
                (e.link() ? linkEdges : elementEdges).add(Map.of("inc", id, "node", e.nodeId(), "role", e.role()));
            }
        }
        for (var edges : List.of(Map.entry("Element", elementEdges), Map.entry("TraceLink", linkEdges))) {
            if (!edges.getValue().isEmpty()) {
                tx.run(EDGES.formatted(edges.getKey()), Map.of("edges", edges.getValue()))
                        .consume();
            }
        }
        long open = tx.run(
                        "MATCH (i:Inconsistency {projectId: $p}) WHERE i.status IN ['OPEN', 'REOPENED'] RETURN count(i) AS n",
                        Map.of("p", projectId))
                .single()
                .get("n")
                .asLong();
        return new Written(created, reopened, resolved, open);
    }

    private static Map<String, Object> resolve(Item item, String reason) {
        return Map.of(
                "id", item.id(),
                "observed", item.status().name(),
                "verification", item.verification(),
                "actor", "system:" + item.props().get("detector"),
                "reason", reason);
    }

    /** Batch element for create/refresh/reopen/touch; observed is the status seen in the snapshot. */
    private static Map<String, Object> item(Prepared p, Item existing, String runId, String now) {
        Finding f = p.finding();
        Map<String, Object> props = new HashMap<>();
        props.put("severity", f.severity());
        props.put("title", f.title());
        props.put("description", f.description());
        props.put("evidence", f.evidence() == null ? null : JSON.writeValueAsString(f.evidence()));
        props.put("confidence", f.confidence());
        props.put("detectorVersion", p.detector().version());
        props.put("evidenceHash", p.evidenceHash());
        if (existing == null) {
            props.put("id", p.id());
            props.put("fingerprint", p.fingerprint());
            props.put("projectId", p.projectId());
            props.put("type", f.type());
            props.put("detector", p.detector().id());
            props.put("firstRunId", runId);
            props.put("lastRunId", runId);
            props.put("status", Status.OPEN.name());
            props.put("verification", "none");
            props.put("createdAt", now);
            props.put("updatedAt", now);
        }
        Map<String, Object> x = new HashMap<>();
        x.put("id", p.id());
        x.put("props", props);
        x.put("observed", existing == null ? null : existing.status().name());
        x.put("actor", "system:" + p.detector().id());
        return x;
    }

    private static List<String> run(
            TransactionContext tx, String cypher, Map<String, Object> params, List<Map<String, Object>> items) {
        if (items.isEmpty()) {
            return List.of();
        }
        Map<String, Object> all = new HashMap<>(params);
        all.put("items", items);
        return tx.run(cypher, all).list(r -> r.get("id").asString());
    }

    private static final String CREATE = """
            MATCH (r:Run {id: $runId})
            UNWIND $items AS x
            CREATE (i:Inconsistency) SET i = x.props
            CREATE (i)-[:HAS_STATUS_CHANGE]->(:StatusChange {to: 'OPEN', actor: x.actor, reason: 'detected',
                    at: $now, runId: $runId})
            CREATE (r)-[:DETECTED]->(i)
            RETURN i.id AS id
            """;
    private static final String REFRESH = """
            MATCH (r:Run {id: $runId})
            UNWIND $items AS x
            MATCH (i:Inconsistency {id: x.id}) WHERE i.status = x.observed
            SET i += x.props, i.lastRunId = $runId
            CREATE (r)-[:DETECTED]->(i)
            RETURN i.id AS id
            """;
    private static final String REOPEN = """
            MATCH (r:Run {id: $runId})
            UNWIND $items AS x
            MATCH (i:Inconsistency {id: x.id}) WHERE i.status = x.observed
            SET i += x.props, i.status = 'REOPENED', i.verification = 'none', i.lastRunId = $runId, i.updatedAt = $now
            CREATE (i)-[:HAS_STATUS_CHANGE]->(:StatusChange {from: x.observed, to: 'REOPENED', actor: x.actor,
                    reason: 're-detected', at: $now, runId: $runId})
            CREATE (r)-[:DETECTED]->(i)
            RETURN i.id AS id
            """;
    private static final String TOUCH = """
            MATCH (r:Run {id: $runId})
            UNWIND $items AS x
            MATCH (i:Inconsistency {id: x.id}) WHERE i.status = x.observed
            SET i.lastRunId = $runId, i.detectorVersion = x.props.detectorVersion
            CREATE (r)-[:DETECTED]->(i)
            RETURN i.id AS id
            """;
    private static final String RESOLVE = """
            UNWIND $items AS x
            MATCH (i:Inconsistency {id: x.id}) WHERE i.status = x.observed AND i.verification = x.verification
            SET i.status = 'RESOLVED', i.verification = 'confirmed', i.updatedAt = $now
            CREATE (i)-[:HAS_STATUS_CHANGE]->(:StatusChange {from: x.observed, to: 'RESOLVED', actor: x.actor,
                    reason: x.reason, at: $now, runId: $runId})
            RETURN i.id AS id
            """;
    private static final String EDGES = """
            UNWIND $edges AS e
            MATCH (i:Inconsistency {id: e.inc}) MATCH (n:%s {id: e.node})
            CREATE (i)-[:INVOLVES {role: e.role}]->(n)
            """;

    private void notify(Prepared p, String id, String status) {
        Finding f = p.finding();
        notifications.accept(JSON.createObjectNode()
                .put("event", "notification")
                .put("projectId", p.projectId())
                .put("inconsistencyId", id)
                .put("type", f.type())
                .put("severity", f.severity())
                .put("status", status)
                .put("title", f.title()));
    }

    private Result result(
            Request req,
            String runId,
            boolean partial,
            boolean whole,
            Set<String> scope,
            Written w,
            ArrayNode detectorRuns) {
        ObjectNode event = JSON.createObjectNode();
        event.put("projectId", req.projectId()).put("runId", runId).put("status", partial ? "partial" : "completed");
        if (!whole) {
            event.putArray("scopeArtifactIds")
                    .addAll(scope.stream()
                            .map(JSON.getNodeFactory()::stringNode)
                            .toList());
        }
        event.set("newIds", ids(w.created()));
        event.set("reopenedIds", ids(w.reopened()));
        event.set("autoResolvedIds", ids(w.resolved()));
        event.put("stillOpenCount", w.stillOpen());
        ObjectNode output = JSON.createObjectNode();
        output.put("runId", runId).put("status", partial ? "partial" : "completed");
        output.set("newIds", event.get("newIds"));
        output.set("reopenedIds", event.get("reopenedIds"));
        output.set("autoResolvedIds", event.get("autoResolvedIds"));
        output.put("stillOpenCount", w.stillOpen());
        output.set("detectorRuns", detectorRuns);
        return new Result(output, event);
    }

    private static ArrayNode ids(List<String> ids) {
        return JSON.createArrayNode()
                .addAll(ids.stream().map(JSON.getNodeFactory()::stringNode).toList());
    }
}
