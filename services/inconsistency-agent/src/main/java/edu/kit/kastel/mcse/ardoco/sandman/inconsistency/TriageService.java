package edu.kit.kastel.mcse.ardoco.sandman.inconsistency;

import edu.kit.kastel.mcse.ardoco.sandman.agent.KgClient;
import edu.kit.kastel.mcse.ardoco.sandman.agent.a2a.EventSink;
import edu.kit.kastel.mcse.ardoco.sandman.inconsistency.InconsistencyStore.Item;
import edu.kit.kastel.mcse.ardoco.sandman.inconsistency.Lifecycle.Status;
import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.Comparator;
import java.util.EnumMap;
import java.util.HashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.stream.Collectors;
import org.neo4j.driver.types.Node;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ArrayNode;
import tools.jackson.databind.node.ObjectNode;

/** Triage API logic: list, detail and user transitions (ADR-0007). */
public final class TriageService {

    /** Request error mapped to an HTTP status (problem+json). */
    public static final class TriageException extends RuntimeException {
        private final int status;

        public TriageException(int status, String message) {
            super(message);
            this.status = status;
        }

        public int status() {
            return status;
        }
    }

    /** List filters; nulls mean no filter. */
    public record Filter(
            Set<Status> status,
            String type,
            String severity,
            String artifactId,
            Instant changedSince,
            int limit,
            int offset) {}

    private static final Logger LOG = LoggerFactory.getLogger(TriageService.class);
    private static final JsonMapper JSON = JsonMapper.shared();

    private static final String TRANSITION = """
            MATCH (i:Inconsistency {id: $id}) WHERE i.status = $expected
            SET i.status = $to, i.updatedAt = $now,
                i.verification = CASE WHEN $to = 'RESOLVED' THEN 'pending' ELSE i.verification END,
                i.acceptedEvidenceHash = CASE WHEN $to IN ['ACCEPTED', 'REJECTED'] THEN i.evidenceHash
                                              ELSE i.acceptedEvidenceHash END
            CREATE (i)-[:HAS_STATUS_CHANGE]->(:StatusChange {from: $expected, to: $to, actor: $actor,
                    reason: $reason, at: $now})
            RETURN i.id AS id
            """;

    private final KgClient kg;
    private final EventSink events;

    public TriageService(KgClient kg, EventSink events) {
        this.kg = kg;
        this.events = events;
    }

    public ObjectNode list(String projectId, Filter f) {
        List<Item> all = InconsistencyStore.loadAll(kg, projectId);
        Map<Status, Long> counts = new EnumMap<>(Status.class);
        all.forEach(i -> counts.merge(i.status(), 1L, Long::sum));
        List<Item> page = all.stream()
                .filter(i -> f.status() == null || f.status().contains(i.status()))
                .filter(i -> f.type() == null || f.type().equals(i.props().get("type")))
                .filter(i ->
                        f.severity() == null || f.severity().equals(i.props().get("severity")))
                .filter(i -> f.artifactId() == null || i.artifactIds().contains(f.artifactId()))
                .filter(i -> f.changedSince() == null
                        || Instant.parse((String) i.props().get("updatedAt")).isAfter(f.changedSince()))
                .sorted(Comparator.comparing((Item i) -> (String) i.props().get("updatedAt"))
                        .reversed()
                        .thenComparing(Item::id))
                .toList();
        List<Item> slice = page.stream().skip(f.offset()).limit(f.limit()).toList();
        Map<String, String> paths = InconsistencyStore.artifactPaths(
                kg, slice.stream().flatMap(i -> i.artifactIds().stream()).collect(Collectors.toSet()));
        ObjectNode out = JSON.createObjectNode();
        ArrayNode items = out.putArray("items");
        slice.forEach(i -> items.add(summary(i, paths)));
        ObjectNode countNode = out.putObject("counts");
        for (Status s : Status.values()) {
            countNode.put(s.name(), counts.getOrDefault(s, 0L));
        }
        if (f.offset() + f.limit() < page.size()) {
            out.put("nextCursor", String.valueOf(f.offset() + f.limit()));
        }
        return out;
    }

    public ObjectNode detail(String id) {
        var rows = kg.read("MATCH (i:Inconsistency {id: $id}) RETURN i", Map.of("id", id));
        if (rows.isEmpty()) {
            throw new TriageException(404, "inconsistency " + id + " not found");
        }
        Map<String, Object> props = ((Node) rows.get(0).get("i")).asMap();
        var involved = kg.read(
                "MATCH (:Inconsistency {id: $id})-[r:INVOLVES]->(x) "
                        + "OPTIONAL MATCH (x)<-[:HAS_ELEMENT]-(v:ArtifactVersion) "
                        + "OPTIONAL MATCH (x)-[:SOURCE]->(s:Element) OPTIONAL MATCH (x)-[:TARGET]->(t:Element) "
                        + "RETURN r.role AS role, labels(x) AS labels, x AS x, v.id AS version, s.key AS sk, t.key AS tk",
                Map.of("id", id));
        var history = kg.read(
                "MATCH (:Inconsistency {id: $id})-[:HAS_STATUS_CHANGE]->(c) RETURN c ORDER BY c.at, id(c)",
                Map.of("id", id));

        Set<String> artifactIds = new LinkedHashSet<>();
        for (var row : involved) {
            Map<String, Object> x = ((Node) row.get("x")).asMap();
            artifactIds.addAll(InconsistencyStore.artifactIdsOf(
                    List.of(x.containsKey("key") ? (String) x.get("key") : row.get("sk") + "->" + row.get("tk"))));
        }
        Map<String, String> paths = InconsistencyStore.artifactPaths(kg, artifactIds);

        ObjectNode out = summary(new Item(props, List.of()), Map.of());
        ArrayNode pathNode = out.putArray("artifactPaths");
        artifactIds.stream().map(paths::get).filter(p -> p != null).distinct().forEach(pathNode::add);
        out.put("description", String.valueOf(props.getOrDefault("description", "")));
        out.put("fingerprint", (String) props.get("fingerprint"));
        if (props.get("evidence") != null) {
            out.set("evidence", JSON.readTree((String) props.get("evidence")));
        }
        ArrayNode inv = out.putArray("involved");
        for (var row : involved) {
            Map<String, Object> x = ((Node) row.get("x")).asMap();
            ObjectNode entry = inv.addObject().put("role", (String) row.get("role"));
            if (x.containsKey("key")) {
                ObjectNode e = entry.putObject("element");
                String artifact = InconsistencyStore.artifactIdsOf(List.of((String) x.get("key")))
                        .iterator()
                        .next();
                @SuppressWarnings("unchecked")
                List<String> labels = (List<String>) row.get("labels");
                e.put("id", (String) x.get("id"))
                        .put("key", (String) x.get("key"))
                        .put(
                                "label",
                                labels.stream()
                                        .filter(l -> !"Element".equals(l))
                                        .findFirst()
                                        .orElse("Element"))
                        .put("name", (String) x.get("name"))
                        .put("artifactPath", paths.getOrDefault(artifact, ""));
                put(e, "artifactVersionId", row.get("version"));
                put(e, "startLine", x.get("startLine"));
                put(e, "endLine", x.get("endLine"));
                put(e, "excerpt", x.get("text"));
            } else {
                entry.putObject("traceLink")
                        .put("id", (String) x.get("id"))
                        .put("approach", (String) x.get("approach"))
                        .put("confidence", ((Number) x.get("confidence")).doubleValue())
                        .put("sourceKey", (String) row.get("sk"))
                        .put("targetKey", (String) row.get("tk"));
            }
        }
        ArrayNode hist = out.putArray("history");
        for (var row : history) {
            Map<String, Object> c = ((Node) row.get("c")).asMap();
            ObjectNode h = hist.addObject()
                    .put("from", (String) c.get("from"))
                    .put("to", (String) c.get("to"))
                    .put("actor", (String) c.get("actor"));
            put(h, "reason", c.get("reason"));
            h.put("at", (String) c.get("at"));
        }
        ArrayNode allowed = out.putArray("allowedTransitions");
        Lifecycle.userTargets(Status.valueOf((String) props.get("status"))).forEach(s -> allowed.add(s.name()));
        return out;
    }

    /** User transition with optimistic concurrency; publishes sandman.inconsistency.status.changed. */
    public ObjectNode transition(String id, Status to, Status expected, String reason, String actor) {
        var rows = kg.read("MATCH (i:Inconsistency {id: $id}) RETURN i.projectId AS p", Map.of("id", id));
        if (rows.isEmpty()) {
            throw new TriageException(404, "inconsistency " + id + " not found");
        }
        if (!Lifecycle.userAllowed(expected, to)) {
            throw new TriageException(422, "transition " + expected + " -> " + to + " is not allowed");
        }
        if (Lifecycle.reasonRequired(to) && (reason == null || reason.isBlank())) {
            throw new TriageException(422, "reason required for " + to);
        }
        String now = Instant.now().truncatedTo(ChronoUnit.SECONDS).toString();
        Map<String, Object> params = new HashMap<>();
        params.put("id", id);
        params.put("expected", expected.name());
        params.put("to", to.name());
        params.put("actor", actor);
        params.put("reason", reason);
        params.put("now", now);
        if (kg.write(TRANSITION, params).isEmpty()) {
            throw new TriageException(409, "status is no longer " + expected);
        }
        ObjectNode data = JSON.createObjectNode()
                .put("projectId", (String) rows.get(0).get("p"))
                .put("inconsistencyId", id)
                .put("from", expected.name())
                .put("to", to.name())
                .put("actor", actor);
        if (reason != null) {
            data.put("reason", reason);
        }
        data.put("at", now);
        try {
            events.publish(
                    "sandman.inconsistency.status.changed",
                    data.get("projectId").asString(),
                    data,
                    null,
                    null,
                    null);
        } catch (RuntimeException e) {
            // ponytail: the transition stays committed; add an outbox if status events must not be lost
            LOG.error("publishing status change of {} failed", id, e);
        }
        return detail(id);
    }

    private static ObjectNode summary(Item i, Map<String, String> paths) {
        Map<String, Object> p = i.props();
        ObjectNode n = JSON.createObjectNode();
        for (String k : List.of("id", "projectId", "type", "severity", "title", "status", "verification")) {
            n.put(k, (String) p.get(k));
        }
        put(n, "confidence", p.get("confidence"));
        n.put("detector", (String) p.get("detector"));
        put(n, "detectorVersion", p.get("detectorVersion"));
        ArrayNode artifacts = n.putArray("artifactPaths");
        i.artifactIds().stream()
                .map(paths::get)
                .filter(x -> x != null)
                .distinct()
                .forEach(artifacts::add);
        n.put("createdAt", (String) p.get("createdAt"));
        n.put("updatedAt", (String) p.get("updatedAt"));
        return n;
    }

    private static void put(ObjectNode n, String name, Object value) {
        if (value != null) {
            n.set(name, JSON.valueToTree(value));
        }
    }
}
