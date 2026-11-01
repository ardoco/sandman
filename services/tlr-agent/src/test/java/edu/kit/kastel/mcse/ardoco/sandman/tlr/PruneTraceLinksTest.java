package edu.kit.kastel.mcse.ardoco.sandman.tlr;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import edu.kit.kastel.mcse.ardoco.sandman.agent.Contracts;
import edu.kit.kastel.mcse.ardoco.sandman.agent.KgClient;
import edu.kit.kastel.mcse.ardoco.sandman.agent.a2a.A2aClient;
import io.quarkus.test.common.http.TestHTTPResource;
import io.quarkus.test.junit.QuarkusTest;
import jakarta.inject.Inject;
import java.net.URI;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.stream.Collectors;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIf;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

@EnabledIf("dockerAvailable")
@QuarkusTest
class PruneTraceLinksTest {

    static final String P = "mini";

    static boolean dockerAvailable() {
        return RecoverTraceLinksTest.dockerAvailable();
    }

    @TestHTTPResource("/")
    URI url;

    @Inject
    KgClient kg;

    @Inject
    TestBeans.Events events;

    @Inject
    Contracts contracts;

    @BeforeEach
    void seed() {
        events.events.clear();
        kg.write("MATCH (n) DETACH DELETE n", Map.of());
        for (String project : List.of(P, "other")) {
            kg.write("CREATE (:Project {id: $p})", Map.of("p", project));
            kg.write(
                    "MATCH (pr:Project {id: $p}) CREATE (pr)-[:HAS_ARTIFACT]->"
                            + "(:Artifact {id: $p + '-code', projectId: $p, kind: 'code'})",
                    Map.of("p", project));
            for (int i = 1; i <= 6; i++) {
                kg.write(
                        "MATCH (a:Artifact {id: $p + '-code'}) CREATE (a)-[:HAS_VERSION]->"
                                + "(v:ArtifactVersion {id: $p + '-v' + $i, projectId: $p, contentHash: $h}) "
                                + "CREATE (:Ingestion {id: $p + '-g' + $i, projectId: $p, seq: $i})-[:ACTIVATED]->(v) "
                                + "CREATE (v)-[:HAS_ELEMENT]->(:Element {id: $p + '-e' + $i, projectId: $p}) "
                                + "WITH a, v WHERE $i = 6 CREATE (a)-[:CURRENT_VERSION]->(v)",
                        Map.of("p", project, "i", i, "h", String.valueOf(i).repeat(64)));
            }
            link(project, "stale", "e1", "e6", false, "proposed", "tlr-x");
            link(project, "explicit", "e1", "e6", false, "proposed", "explicit-reference");
            link(project, "declared", "e3", "e6", false, "confirmed", "user-declared");
            link(project, "involved", "e2", "e6", false, "proposed", "tlr-x");
            link(project, "active", "e3", "e6", true, "proposed", "tlr-x");
            link(project, "confirmed", "e4", "e6", false, "confirmed", "tlr-x");
            link(project, "rejected", "e5", "e6", true, "rejected", "tlr-x");
            kg.write(
                    "CREATE (:TraceLink {id: $p + '-orphan', projectId: $p, approach: 'tlr-x', active: false, "
                            + "status: 'proposed'})",
                    Map.of("p", project));
            kg.write(
                    "MATCH (l:TraceLink {id: $p + '-stale'}) CREATE (:Run {id: $p + '-run', projectId: $p, "
                            + "agent: $a, status: 'completed'})-[:PRODUCED]->(l) SET l.runId = $p + '-run'",
                    Map.of("p", project, "a", "tlr-agent"));
            kg.write(
                    "MATCH (l:TraceLink {id: $p + '-involved'}) "
                            + "CREATE (:Inconsistency {id: $p + '-inc', projectId: $p})-[:INVOLVES]->(l)",
                    Map.of("p", project));
        }
    }

    private void link(String p, String id, String src, String tgt, boolean active, String status, String approach) {
        kg.write(
                "MATCH (s:Element {id: $p + '-' + $s}), (t:Element {id: $p + '-' + $t}) "
                        + "CREATE (l:TraceLink {id: $p + '-' + $id, projectId: $p, approach: $approach, active: $active, "
                        + "status: $status}) CREATE (l)-[:SOURCE]->(s) CREATE (l)-[:TARGET]->(t)",
                Map.of("p", p, "id", id, "s", src, "t", tgt, "active", active, "status", status, "approach", approach));
    }

    JsonNode prune(String json) throws Exception {
        try (A2aClient client = new A2aClient(TestBeans.TOKENS.get("orchestrator"))) {
            var task = client.submit(
                    url.toString(),
                    "prune-trace-links",
                    JsonMapper.shared().readTree("{\"projectId\":\"" + P + "\"" + json + "}"),
                    null,
                    null,
                    UUID.randomUUID().toString());
            for (int i = 0; i < 1000 && !task.terminal(); i++) {
                Thread.sleep(10);
                task = client.get(url.toString(), task.taskId());
            }
            assertEquals("TASK_STATE_COMPLETED", task.state(), String.valueOf(task));
            contracts.validate("skills.schema.json#/$defs/PruneTraceLinksOutput", task.result());
            return task.result();
        }
    }

    Set<String> ids(String label, String project) {
        return kg.read("MATCH (n:" + label + " {projectId: $p}) RETURN n.id AS id", Map.of("p", project)).stream()
                .map(r -> (String) r.get("id"))
                .collect(Collectors.toSet());
    }

    @Test
    void dryRunDeletesNothing() throws Exception {
        Set<String> before = ids("TraceLink", P);
        JsonNode result = prune(",\"keepVersionsPerArtifact\":1");
        assertTrue(result.get("dryRun").asBoolean());
        assertEquals(2, result.at("/removed/links").asInt());
        assertEquals(1, result.at("/removed/runs").asInt());
        assertEquals(before, ids("TraceLink", P));
        assertEquals(1, events.events.size());
        assertEquals("sandman.retention.pruned", events.events.get(0).type());
        contracts.validate(
                "events.schema.json#/$defs/RetentionPruned",
                events.events.get(0).data());
    }

    @Test
    void removesOnlyUnreferencedAndIsIdempotent() throws Exception {
        JsonNode result = prune(",\"keepVersionsPerArtifact\":1,\"dryRun\":false");
        assertEquals(
                2,
                result.get("removedCount").asInt() - result.at("/removed/runs").asInt());
        assertEquals(
                Set.of(
                        P + "-explicit",
                        P + "-declared",
                        P + "-involved",
                        P + "-active",
                        P + "-confirmed",
                        P + "-rejected"),
                ids("TraceLink", P));
        assertEquals(
                Set.of(),
                ids("Run", P).stream().filter(i -> i.equals(P + "-run")).collect(Collectors.toSet()));
        assertEquals(8, ids("TraceLink", "other").size());
        assertEquals(1, ids("Run", "other").size());
        JsonNode second = prune(",\"keepVersionsPerArtifact\":1,\"dryRun\":false");
        assertEquals(0, second.at("/removed/links").asInt());
        assertEquals(0, second.at("/removed/runs").asInt());
    }

    @Test
    void protectedContentHashKeepsLinks() throws Exception {
        JsonNode result = prune(",\"keepVersionsPerArtifact\":1,\"dryRun\":false,\"protectedContentHashes\":[\""
                + "1".repeat(64) + "\"]");
        assertEquals(1, result.at("/removed/links").asInt());
        assertTrue(ids("TraceLink", P).contains(P + "-stale"));
    }
}
