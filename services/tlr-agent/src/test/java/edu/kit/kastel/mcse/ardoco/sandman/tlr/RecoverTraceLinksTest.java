package edu.kit.kastel.mcse.ardoco.sandman.tlr;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import edu.kit.kastel.mcse.ardoco.sandman.agent.Contracts;
import edu.kit.kastel.mcse.ardoco.sandman.agent.Ids;
import edu.kit.kastel.mcse.ardoco.sandman.agent.Jcs;
import edu.kit.kastel.mcse.ardoco.sandman.agent.KgClient;
import edu.kit.kastel.mcse.ardoco.sandman.agent.RunRecorder;
import edu.kit.kastel.mcse.ardoco.sandman.agent.Settings;
import edu.kit.kastel.mcse.ardoco.sandman.agent.a2a.A2aClient;
import io.quarkus.test.common.http.TestHTTPResource;
import io.quarkus.test.junit.QuarkusTest;
import jakarta.inject.Inject;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import org.a2aproject.sdk.spec.DataPart;
import org.a2aproject.sdk.spec.Message;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIf;
import org.testcontainers.DockerClientFactory;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

@EnabledIf("dockerAvailable")
@QuarkusTest
class RecoverTraceLinksTest {

    static final String PROJECT = "mini";
    static final String SRC_VERSION = Ids.stableId("ver", "requirements");
    static final String TGT_VERSION = Ids.stableId("ver", "code");
    static final String REQ = Ids.stableId("el", SRC_VERSION, "REQ-1");
    static final String MEDIA = Ids.stableId("el", TGT_VERSION, "MediaCache");
    static final String USER = Ids.stableId("el", TGT_VERSION, "UserDB");
    static final String STUB = "stub-name-match";

    static boolean dockerAvailable() {
        // Quarkus sets its own context class loader, which breaks Testcontainers' ServiceLoader lookup
        Thread thread = Thread.currentThread();
        ClassLoader quarkus = thread.getContextClassLoader();
        thread.setContextClassLoader(DockerClientFactory.class.getClassLoader());
        try {
            return DockerClientFactory.instance().isDockerAvailable();
        } finally {
            thread.setContextClassLoader(quarkus);
        }
    }

    @TestHTTPResource("/")
    URI url;

    @Inject
    KgClient kg;

    @Inject
    TestBeans.Events events;

    @Inject
    Contracts contracts;

    @Inject
    Settings settings;

    @Inject
    Approaches approaches;

    @Inject
    Beans beans;

    final List<A2aClient> clients = new ArrayList<>();

    @BeforeEach
    void seed() {
        events.events.clear();
        kg.write("MATCH (n) DETACH DELETE n", Map.of());
        kg.write("CREATE (:Project {id: $p, llmPolicy: 'cloud-allowed'})", Map.of("p", PROJECT));
        artifact("requirements", SRC_VERSION, Map.of(REQ, List.of("Requirement", "Cache media files", "req#REQ-1")));
        artifact(
                "code",
                TGT_VERSION,
                Map.of(
                        MEDIA, List.of("CodeUnit", "MediaCache", "code#MediaCache"),
                        USER, List.of("CodeUnit", "UserDB", "code#UserDB")));
    }

    @AfterEach
    void close() {
        clients.forEach(A2aClient::close);
    }

    private void artifact(String kind, String versionId, Map<String, List<String>> elements) {
        kg.write(
                "CREATE (:Artifact {id: $a, projectId: $p, kind: $k})-[:HAS_VERSION]->"
                        + "(:ArtifactVersion {id: $v, projectId: $p})",
                Map.of("a", Ids.stableId("art", kind), "p", PROJECT, "k", kind, "v", versionId));
        elements.forEach((id, e) -> kg.write(
                "MATCH (v:ArtifactVersion {id: $v}) CREATE (v)-[:HAS_ELEMENT]->(:Element:" + e.get(0)
                        + " {id: $id, projectId: $p, key: $key, name: $name, kind: 'file'})",
                Map.of("v", versionId, "id", id, "p", PROJECT, "key", e.get(2), "name", e.get(1))));
    }

    // --- helpers

    JsonNode input(String json) {
        return JsonMapper.shared()
                .readTree("{\"projectId\":\"" + PROJECT + "\",\"sourceVersionIds\":[\"" + SRC_VERSION
                        + "\"],\"targetVersionIds\":[\"" + TGT_VERSION + "\"]" + json + "}");
    }

    A2aClient.TaskSnapshot run(JsonNode input, String messageId) throws InterruptedException {
        A2aClient client = new A2aClient(TestBeans.TOKENS.get("orchestrator"));
        clients.add(client);
        var task = client.submit(url.toString(), "recover-trace-links", input, null, null, messageId);
        for (int i = 0; i < 1000 && !task.terminal(); i++) {
            Thread.sleep(10);
            task = client.get(url.toString(), task.taskId());
        }
        assertTrue(task.terminal(), "task did not finish: " + task);
        return task;
    }

    A2aClient.TaskSnapshot run(JsonNode input) throws InterruptedException {
        return run(input, UUID.randomUUID().toString());
    }

    List<Map<String, Object>> links() {
        return kg.read(
                "MATCH (l:TraceLink) RETURN l.id AS id, l.active AS active, l.confidence AS confidence, "
                        + "l.runId AS runId ORDER BY id",
                Map.of());
    }

    List<Map<String, Object>> activeLinks() {
        return links().stream().filter(l -> (Boolean) l.get("active")).toList();
    }

    static String configHash(String paramsJson) {
        return Ids.sha256Hex(Jcs.canonicalize(JsonMapper.shared().readTree(paramsJson)));
    }

    static String linkId(String configHash, String sourceId, String targetId) {
        return Ids.stableId("tl", STUB, "1", configHash, sourceId, targetId);
    }

    static final String DEFAULT_HASH = configHash("{\"minTokenLength\":4,\"confidence\":0.5}");

    // --- tests

    @Test
    void createsOneActiveLinkRunAndEvent() throws Exception {
        var done = run(input(",\"approaches\":[\"" + STUB + "\"]"), "m-acceptance");

        assertEquals("TASK_STATE_COMPLETED", done.state(), String.valueOf(done.error()));
        contracts.validate("skills.schema.json#/$defs/RecoverTraceLinksOutput", done.result());
        assertEquals(1, done.result().at("/runs/0/linkCount").asInt());

        var active = activeLinks();
        assertEquals(1, active.size());
        assertEquals(linkId(DEFAULT_HASH, REQ, MEDIA), active.get(0).get("id"));
        assertEquals(1, links().size());

        var runs = kg.read(
                "MATCH (r:Run {tool: $t}) RETURN r.status AS status, r.configHash AS hash, r.skill AS skill, "
                        + "r.a2aTaskId AS task, r.id AS id",
                Map.of("t", STUB));
        assertEquals(1, runs.size());
        assertEquals("completed", runs.get(0).get("status"));
        assertEquals(DEFAULT_HASH, runs.get(0).get("hash"));
        assertEquals(done.taskId(), runs.get(0).get("task"));
        assertEquals(runs.get(0).get("id"), done.result().at("/runs/0/runId").asString());

        assertEquals(1, events.events.size());
        var event = events.events.get(0);
        assertEquals("sandman.tracelinks.updated", event.type());
        assertEquals("m-acceptance", event.causationId());
        assertEquals(PROJECT, event.subject());
        contracts.validate("events.schema.json#/$defs/TracelinksUpdated", event.data());
        assertEquals(done.result().get("runs"), event.data().get("runs"));
    }

    @Test
    void defaultSelectionRunsAllEnabledApproachesAndIsolatesFailures() throws Exception {
        var done = run(input(""));

        assertEquals("TASK_STATE_COMPLETED", done.state(), String.valueOf(done.error()));
        contracts.validate("skills.schema.json#/$defs/RecoverTraceLinksOutput", done.result());
        Map<String, JsonNode> byApproach = new java.util.HashMap<>();
        done.result().get("runs").forEach(r -> byApproach.put(r.get("approach").asString(), r));
        assertEquals(2, byApproach.size());
        assertEquals("completed", byApproach.get(STUB).get("status").asString());
        assertEquals("failed", byApproach.get("test-failing").get("status").asString());
        assertTrue(byApproach.get("test-failing").get("error").asString().contains("boom"));
        assertEquals(1, activeLinks().size());
        assertEquals(
                "failed",
                kg.read("MATCH (r:Run {tool: 'test-failing'}) RETURN r.status AS s", Map.of())
                        .get(0)
                        .get("s"));
        assertEquals(1, events.events.size());
        assertEquals(2, events.events.get(0).data().get("runs").size());
    }

    @Test
    void sameInputTwiceKeepsOneActiveLink() throws Exception {
        var in = input(",\"approaches\":[\"" + STUB + "\"]");
        run(in);
        var firstId = links().get(0).get("id");
        var second = run(in);

        assertEquals("TASK_STATE_COMPLETED", second.state());
        assertEquals(1, second.result().at("/runs/0/linkCount").asInt());
        assertEquals(1, links().size());
        assertEquals(
                List.of(firstId), activeLinks().stream().map(l -> l.get("id")).toList());
        assertEquals(
                2L,
                kg.read("MATCH (:Run {tool: $t, status: 'completed'}) RETURN count(*) AS n", Map.of("t", STUB))
                        .get(0)
                        .get("n"));
    }

    @Test
    void changedParamsLeaveOnlyTheNewLinksActive() throws Exception {
        run(input(",\"approaches\":[\"" + STUB + "\"]"));
        var done = run(input(",\"approaches\":[\"" + STUB + "\"],\"params\":{\"" + STUB + "\":{\"confidence\":0.9}}"));

        assertEquals("TASK_STATE_COMPLETED", done.state());
        assertEquals(2, links().size());
        var active = activeLinks();
        assertEquals(1, active.size());
        assertEquals(
                linkId(configHash("{\"minTokenLength\":4,\"confidence\":0.9}"), REQ, MEDIA),
                active.get(0).get("id"));
        assertEquals(0.9, (Double) active.get(0).get("confidence"), 1e-9);
    }

    @Test
    void linksOfAnInterruptedRunAreNeverActive() {
        var store = new LinkStore(kg);
        var recorder = new RunRecorder(kg, "tlr-agent", "test");
        String runId = recorder.start(RunRecorder.RunInfo.of(PROJECT, "recover-trace-links", STUB, "1", "key-1"));
        var link = new java.util.HashMap<String, Object>();
        link.put("id", linkId("cfg", REQ, MEDIA));
        link.put("sourceId", REQ);
        link.put("targetId", MEDIA);
        link.put("linkType", "relates");
        link.put("confidence", 0.5);
        link.put("rationale", null);
        store.writeInactive(runId, PROJECT, new StubNameMatch(), "cfg", List.of(link)); // killed before finish()

        assertEquals(1, links().size());
        assertTrue(activeLinks().isEmpty());
        assertEquals(1L, recorder.cleanupStale("key-1")); // the retry removes them
        assertTrue(links().isEmpty());
    }

    @Test
    void writesInBatches() {
        var recorder = new RunRecorder(kg, "tlr-agent", "test");
        String runId = recorder.start(RunRecorder.RunInfo.of(PROJECT, "recover-trace-links", STUB, "1", "key-2"));
        // one link per source/target pair of the seeded elements, repeated ids collapse via MERGE
        List<Map<String, Object>> batch = new ArrayList<>();
        for (int i = 0; i < LinkStore.BATCH_SIZE + 1; i++) {
            var l = new java.util.HashMap<String, Object>();
            l.put("id", Ids.stableId("tl", "n" + i));
            l.put("sourceId", REQ);
            l.put("targetId", i % 2 == 0 ? MEDIA : USER);
            l.put("linkType", "relates");
            l.put("confidence", 0.5);
            batch.add(l);
        }
        new LinkStore(kg).writeInactive(runId, PROJECT, new StubNameMatch(), "cfg", batch);
        assertEquals(LinkStore.BATCH_SIZE + 1, links().size());
        assertTrue(activeLinks().isEmpty());
        new LinkStore(kg).finish(runId, PROJECT, STUB, List.of(SRC_VERSION), List.of(TGT_VERSION));
        assertEquals(LinkStore.BATCH_SIZE + 1, activeLinks().size());
    }

    @Test
    void unknownApproachAndUnknownVersionsAreInvalidInput() throws Exception {
        var unknownApproach = run(input(",\"approaches\":[\"nope\"]"));
        assertEquals("TASK_STATE_FAILED", unknownApproach.state());
        assertEquals("INVALID_INPUT", unknownApproach.error().get("code").asString());

        var unknownVersion = run(JsonMapper.shared()
                .readTree("{\"projectId\":\"" + PROJECT + "\",\"sourceVersionIds\":[\"ver_" + "0".repeat(32)
                        + "\"],\"targetVersionIds\":[\"" + TGT_VERSION + "\"]}"));
        assertEquals("INVALID_INPUT", unknownVersion.error().get("code").asString());
        assertTrue(events.events.isEmpty());
    }

    @Test
    void invalidApproachesConfigFailsAtStartup(@org.junit.jupiter.api.io.TempDir java.nio.file.Path dir)
            throws Exception {
        var file = dir.resolve("approaches.yaml");
        java.nio.file.Files.writeString(file, "approaches:\n  stub-name-match: { enabled: \"yes\" }\n");
        var e = org.junit.jupiter.api.Assertions.assertThrows(
                Settings.ConfigException.class, () -> new Approaches(file, List.of(), contracts));
        assertTrue(e.getMessage().contains("invalid config " + file), e.getMessage());
    }

    @Test
    void startupDeactivatesLinksOfDisabledApproachesOnly() {
        kg.write(
                "CREATE (:TraceLink {id: 'tl_a', approach: 'lissa', active: true}), "
                        + "(:TraceLink {id: 'tl_b', approach: 'explicit-reference', active: true}), "
                        + "(:TraceLink {id: 'tl_c', approach: 'stub-name-match', active: true})",
                Map.of());
        beans.onStart(null, kg, settings, approaches);
        var active = activeLinks().stream().map(l -> l.get("id")).toList();
        assertEquals(List.of("tl_b", "tl_c"), active);
    }

    @Test
    void explicitReferenceLinksSurviveCleanupAndRecovery() throws Exception {
        kg.write(
                "CREATE (:TraceLink {id: 'tl_explicit', approach: 'explicit-reference', active: true, projectId: $p})",
                Map.of("p", PROJECT));
        new LinkStore(kg).deactivate(List.of("explicit-reference", "lissa"));
        assertEquals(
                List.of("tl_explicit"),
                activeLinks().stream().map(l -> l.get("id")).toList());
        var task = run(input(""));
        assertEquals("TASK_STATE_COMPLETED", task.state(), String.valueOf(task.error()));
        new LinkStore(kg).finish("run_none", PROJECT, "explicit-reference", List.of(SRC_VERSION), List.of(TGT_VERSION));
        assertTrue(activeLinks().stream().anyMatch(l -> "tl_explicit".equals(l.get("id"))));
    }

    @Test
    void plannedReviewEndpointAnswers501AndActorFromNonForwarderIs403() throws Exception {
        HttpClient http = HttpClient.newHttpClient();
        URI uri = url.resolve("/v1/links/tl_" + "0".repeat(32) + "/review");
        var body = HttpRequest.BodyPublishers.ofString("{\"status\":\"confirmed\"}");
        var qs = http.send(
                HttpRequest.newBuilder(uri)
                        .header("Authorization", "Bearer " + TestBeans.TOKENS.get("query-service"))
                        .header("X-Sandman-Actor", "user:alice")
                        .POST(body)
                        .build(),
                HttpResponse.BodyHandlers.ofString());
        assertEquals(501, qs.statusCode());
        JsonNode problem = JsonMapper.shared().readTree(qs.body());
        assertEquals(501, problem.get("status").asInt());
        assertEquals("Not Implemented", problem.get("title").asString());
        var orch = http.send(
                HttpRequest.newBuilder(uri)
                        .header("Authorization", "Bearer " + TestBeans.TOKENS.get("orchestrator"))
                        .header("X-Sandman-Actor", "user:alice")
                        .POST(body)
                        .build(),
                HttpResponse.BodyHandlers.discarding());
        assertEquals(403, orch.statusCode());
        assertEquals(
                401,
                http.send(HttpRequest.newBuilder(uri).POST(body).build(), HttpResponse.BodyHandlers.discarding())
                        .statusCode());
    }

    @Test
    void skillInvocationVectors() throws Exception {
        A2aClient client = new A2aClient(TestBeans.TOKENS.get("orchestrator"));
        clients.add(client);
        JsonNode cases = JsonMapper.shared()
                .readTree(settings.contractsDir().resolve("conformance/vectors.json"))
                .get("skillInvocation")
                .get("cases");
        assertEquals(4, cases.size());
        for (JsonNode c : cases) {
            JsonNode m = c.get("message");
            Message message = Message.builder()
                    .messageId(m.get("messageId").asString())
                    .role(Message.Role.ROLE_USER)
                    .metadata(JsonMapper.shared().convertValue(m.get("metadata"), Map.class))
                    .parts(new DataPart(JsonMapper.shared()
                            .convertValue(m.get("parts").get(0).get("data"), Map.class)))
                    .build();
            var task = client.send(url.toString(), message);
            for (int i = 0; i < 1000 && !task.terminal(); i++) {
                Thread.sleep(10);
                task = client.get(url.toString(), task.taskId());
            }
            String name = c.get("name").asString();
            if ("TASK_STATE_REJECTED".equals(c.get("expectedState").asString())) {
                assertEquals("TASK_STATE_REJECTED", task.state(), name);
                assertEquals(
                        c.get("expectedErrorCode").asString(),
                        task.error().get("code").asString(),
                        name);
                assertFalse(task.error().get("retryable").asBoolean(), name);
            } else {
                // the vector's versions do not exist in the KG, so the task may fail; it must not be rejected
                assertFalse("TASK_STATE_REJECTED".equals(task.state()), name + ": " + task);
            }
        }
    }

    @Test
    void approachesEndpointMatchesTheContract() throws Exception {
        HttpClient http = HttpClient.newHttpClient();
        URI uri = url.resolve("/v1/approaches");
        assertEquals(
                401,
                http.send(HttpRequest.newBuilder(uri).build(), HttpResponse.BodyHandlers.discarding())
                        .statusCode());
        var response = http.send(
                HttpRequest.newBuilder(uri)
                        .header("Authorization", "Bearer " + TestBeans.TOKENS.get("orchestrator"))
                        .build(),
                HttpResponse.BodyHandlers.ofString());
        assertEquals(200, response.statusCode());
        JsonNode body = JsonMapper.shared().readTree(response.body());
        contracts.validate("skills.schema.json#/$defs/ListApproachesOutput", body);
        var stub = body.get("approaches")
                .valueStream()
                .filter(a -> STUB.equals(a.get("id").asString()))
                .findFirst()
                .orElseThrow();
        assertEquals("1", stub.get("version").asString());
        assertEquals(36, stub.get("kindPairs").size());
        assertFalse(stub.get("usesLlm").asBoolean());
        assertTrue(body.get("approaches")
                .valueStream()
                .noneMatch(a -> "lissa".equals(a.get("id").asString())));
    }
}
