package edu.kit.kastel.mcse.ardoco.sandman.tlr;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotEquals;
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
import java.util.stream.Stream;
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
        int finished = FailingApproach.FINISHED.get();
        var done = run(input(""));
        assertEquals(finished + 1, FailingApproach.FINISHED.get(), "callFinished once per skill call");

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
    void unavailableApproachFailsTheSkillRetryableAfterTheOthersWroteTheirLinks() throws Exception {
        var done = run(input(",\"approaches\":[\"test-unavailable\",\"" + STUB + "\"]"));

        assertEquals("TASK_STATE_FAILED", done.state());
        assertEquals("DEPENDENCY_UNAVAILABLE", done.error().get("code").asString());
        assertTrue(done.error().get("retryable").asBoolean());
        assertTrue(done.error().get("message").asString().contains("test-unavailable: dependency down"));
        assertEquals(1, activeLinks().size());
        assertEquals(
                "failed",
                kg.read("MATCH (r:Run {tool: 'test-unavailable'}) RETURN r.status AS s", Map.of())
                        .get(0)
                        .get("s"));
        assertEquals(
                "completed",
                kg.read("MATCH (r:Run {tool: $t}) RETURN r.status AS s", Map.of("t", STUB))
                        .get(0)
                        .get("s"));
        assertEquals(0, events.events.size());
    }

    @Test
    void unavailableEnvironmentFailsTheSkillRetryableWithoutARunOrAHash() throws Exception {
        var done = run(input(",\"approaches\":[\"test-environment-down\",\"" + STUB + "\"]"));

        assertEquals("TASK_STATE_FAILED", done.state());
        assertEquals("DEPENDENCY_UNAVAILABLE", done.error().get("code").asString());
        assertTrue(done.error().get("retryable").asBoolean());
        assertTrue(done.error().get("message").asString().contains("test-environment-down: environment down"));
        assertEquals(
                0,
                kg.read("MATCH (r:Run {tool: 'test-environment-down'}) RETURN r.status AS s", Map.of())
                        .size());
        assertEquals(1, activeLinks().size());
    }

    @Test
    void environmentOfTheApproachEntersTheConfigHash() throws Exception {
        run(input(",\"approaches\":[\"test-unavailable\"]"));
        var hash = kg.read("MATCH (r:Run {tool: 'test-unavailable'}) RETURN r.configHash AS h", Map.of())
                .get(0)
                .get("h");
        var params = JsonMapper.shared().createObjectNode();
        assertNotEquals(Ids.sha256Hex(Jcs.canonicalize(params)), hash);
        params.put("environment", "dependency=down");
        assertEquals(Ids.sha256Hex(Jcs.canonicalize(params)), hash);
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

    static final String V_D1 = Ids.stableId("ver", "d1");
    static final String V_M1 = Ids.stableId("ver", "m1");
    static final String V_M1B = Ids.stableId("ver", "m1b");
    static final String V_M2 = Ids.stableId("ver", "m2");

    private void wholeArtifact(
            String artifactId, String kind, String versionId, boolean current, List<String> elements) {
        kg.write(
                "MERGE (a:Artifact {id: $a}) SET a.projectId = $p, a.kind = $k, a.path = $a, a.format = 'x' "
                        + "CREATE (a)-[:HAS_VERSION]->(v:ArtifactVersion {id: $v, projectId: $p, storageUri: 'artifact://mini/' + $v})",
                Map.of("a", artifactId, "p", PROJECT, "k", kind, "v", versionId));
        if (current) {
            kg.write(
                    "MATCH (a:Artifact {id: $a}) OPTIONAL MATCH (a)-[c:CURRENT_VERSION]->() DELETE c "
                            + "WITH a MATCH (v:ArtifactVersion {id: $v}) CREATE (a)-[:CURRENT_VERSION]->(v)",
                    Map.of("a", artifactId, "v", versionId));
        }
        for (String e : elements) { // "name|text"
            String[] parts = e.split("\\|", -1);
            kg.write(
                    "MATCH (v:ArtifactVersion {id: $v}) CREATE (v)-[:HAS_ELEMENT]->(:Element {id: $id, projectId: $p, "
                            + "key: $key, name: $name, text: $text, kind: 'x'})",
                    Map.of(
                            "v",
                            versionId,
                            "id",
                            Ids.stableId("el", versionId, parts[0]),
                            "p",
                            PROJECT,
                            "key",
                            artifactId + "#" + parts[0],
                            "name",
                            parts[0],
                            "text",
                            parts[1]));
        }
    }

    private List<String> eventIds(int event, String member) {
        return events.events
                .get(event)
                .data()
                .get(member)
                .valueStream()
                .map(JsonNode::asString)
                .toList();
    }

    private List<String> activeWholeLinks() {
        return kg
                .read(
                        "MATCH (l:TraceLink {approach: 'test-whole', active: true})-[:SOURCE]->(s:Element), (l)-[:TARGET]->(t:Element) "
                                + "RETURN s.id + '>' + t.id AS pair ORDER BY pair",
                        Map.of())
                .stream()
                .map(r -> (String) r.get("pair"))
                .toList();
    }

    @Test
    void wholeProjectApproachUsesAllCurrentVersionsAndRetiresOwnStaleLinks() throws Exception {
        wholeArtifact("art_doc", "architecture-doc", V_D1, true, List.of("s1|uses Alpha", "s2|uses Beta"));
        wholeArtifact("art_m1", "architecture-model", V_M1, true, List.of("Alpha|", "Beta|"));
        JsonNode in = JsonMapper.shared()
                .readTree("{\"projectId\":\"" + PROJECT + "\",\"sourceVersionIds\":[\"" + V_D1 + "\"],"
                        + "\"targetVersionIds\":[\"" + V_M1 + "\"],\"approaches\":[\"test-whole\"]}");
        assertEquals("TASK_STATE_COMPLETED", run(in).state());
        assertEquals(2, activeWholeLinks().size());
        assertEquals(List.of(V_D1), eventIds(0, "sourceVersionIds"));
        assertEquals(List.of(V_M1), eventIds(0, "targetVersionIds"));

        // a second model that the request does not mention: ignored subset, all current versions are used
        wholeArtifact("art_m2", "architecture-model", V_M2, true, List.of("Alpha|"));
        var done = run(in);
        assertEquals("TASK_STATE_COMPLETED", done.state(), String.valueOf(done.error()));
        assertEquals(3, activeWholeLinks().size());
        assertEquals(List.of(V_D1), eventIds(1, "sourceVersionIds"));
        assertEquals(List.of(V_M1, V_M2), eventIds(1, "targetVersionIds"));
        contracts.validate(
                "events.schema.json#/$defs/TracelinksUpdated",
                events.events.get(1).data());
        assertEquals(
                List.of(V_D1, V_M1, V_M2).stream().sorted().toList(),
                kg
                        .read(
                                "MATCH (r:Run {tool: 'test-whole', a2aTaskId: $t})-[:USED]->(v) RETURN v.id AS id ORDER BY id",
                                Map.of("t", done.taskId()))
                        .stream()
                        .map(r -> (String) r.get("id"))
                        .toList());
        assertEquals(1, done.result().get("runs").size());

        // new model version without Beta: its links and the links to the replaced version go; same input twice is
        // stable
        wholeArtifact("art_m1", "architecture-model", V_M1B, true, List.of("Alpha|"));
        run(in);
        assertEquals(List.of(V_D1), eventIds(2, "sourceVersionIds"));
        assertEquals(List.of(V_M1, V_M1B), eventIds(2, "targetVersionIds"));
        var after = activeWholeLinks();
        assertEquals(
                Stream.of(
                                Ids.stableId("el", V_D1, "s1") + ">" + Ids.stableId("el", V_M1B, "Alpha"),
                                Ids.stableId("el", V_D1, "s1") + ">" + Ids.stableId("el", V_M2, "Alpha"))
                        .sorted()
                        .toList(),
                after);
        run(in);
        assertEquals(after, activeWholeLinks());

        // deleting the last model retires everything
        kg.write("MATCH (:Artifact {id: 'art_m1'})-[c:CURRENT_VERSION]->() DELETE c", Map.of());
        kg.write("MATCH (:Artifact {id: 'art_m2'})-[c:CURRENT_VERSION]->() DELETE c", Map.of());
        run(in);
        assertTrue(activeWholeLinks().isEmpty());
    }

    private A2aClient.TaskSnapshot submit(JsonNode in) {
        A2aClient client = new A2aClient(TestBeans.TOKENS.get("orchestrator"));
        clients.add(client);
        return client.submit(
                url.toString(),
                "recover-trace-links",
                in,
                null,
                null,
                UUID.randomUUID().toString());
    }

    private A2aClient.TaskSnapshot await(A2aClient.TaskSnapshot task) throws InterruptedException {
        for (int i = 0; i < 1000 && !task.terminal(); i++) {
            Thread.sleep(10);
            task = new A2aClient(TestBeans.TOKENS.get("orchestrator")).get(url.toString(), task.taskId());
        }
        assertTrue(task.terminal(), "task did not finish: " + task);
        return task;
    }

    private JsonNode wholeInput() {
        return JsonMapper.shared()
                .readTree("{\"projectId\":\"" + PROJECT + "\",\"sourceVersionIds\":[\"" + V_D1 + "\"],"
                        + "\"targetVersionIds\":[\"" + V_M1 + "\"],\"approaches\":[\"test-whole\"]}");
    }

    record Gate(java.util.concurrent.CountDownLatch entered, java.util.concurrent.CountDownLatch release) {}

    /** The first whole-project run stays in the approach until the gate is released. */
    private Gate holdFirstWholeRun() {
        var gate = new Gate(new java.util.concurrent.CountDownLatch(1), new java.util.concurrent.CountDownLatch(1));
        var first = new java.util.concurrent.atomic.AtomicBoolean(true);
        WholeProjectApproach.beforeRecover = () -> {
            if (first.getAndSet(false)) {
                gate.entered().countDown();
                try {
                    gate.release().await();
                } catch (InterruptedException e) {
                    Thread.currentThread().interrupt();
                }
            }
        };
        return gate;
    }

    @Test
    void overlappingWholeProjectRunsEndInTheStateOfTheNewestSnapshot() throws Exception {
        wholeArtifact("art_doc", "architecture-doc", V_D1, true, List.of("s1|uses Alpha", "s2|uses Beta"));
        wholeArtifact("art_m1", "architecture-model", V_M1, true, List.of("Alpha|", "Beta|"));
        var gate = holdFirstWholeRun();
        try {
            var a = submit(wholeInput());
            gate.entered().await(); // run A holds its snapshot (doc + m1) inside the approach
            wholeArtifact("art_m2", "architecture-model", V_M2, true, List.of("Alpha|"));
            var b = submit(wholeInput());
            Thread.sleep(300);
            gate.release().countDown();
            assertEquals("TASK_STATE_COMPLETED", await(a).state());
            assertEquals("TASK_STATE_COMPLETED", await(b).state());
        } finally {
            WholeProjectApproach.beforeRecover = null;
        }
        assertEquals(
                Stream.of(
                                Ids.stableId("el", V_D1, "s1") + ">" + Ids.stableId("el", V_M1, "Alpha"),
                                Ids.stableId("el", V_D1, "s2") + ">" + Ids.stableId("el", V_M1, "Beta"),
                                Ids.stableId("el", V_D1, "s1") + ">" + Ids.stableId("el", V_M2, "Alpha"))
                        .sorted()
                        .toList(),
                activeWholeLinks());
    }

    @Test
    void cancellingATaskWaitingForTheProjectLockEndsCanceledWithoutARun() throws Exception {
        wholeArtifact("art_doc", "architecture-doc", V_D1, true, List.of("s1|uses Alpha"));
        wholeArtifact("art_m1", "architecture-model", V_M1, true, List.of("Alpha|"));
        var gate = holdFirstWholeRun();
        try {
            var a = submit(wholeInput());
            gate.entered().await();
            var b = submit(wholeInput());
            Thread.sleep(300);
            var client = new A2aClient(TestBeans.TOKENS.get("orchestrator"));
            clients.add(client);
            client.cancel(url.toString(), b.taskId());
            assertEquals("TASK_STATE_CANCELED", await(b).state());
            Thread.sleep(300);
            gate.release().countDown();
            assertEquals("TASK_STATE_COMPLETED", await(a).state());
        } finally {
            WholeProjectApproach.beforeRecover = null;
        }
        assertEquals(
                1,
                kg.read("MATCH (r:Run {tool: 'test-whole'}) RETURN r.id AS id", Map.of())
                        .size());
        assertEquals(1, events.events.size());
    }

    @Test
    void parametersOutsideTheApproachAllowlistAreInvalidInput() throws Exception {
        var done = run(
                input(",\"approaches\":[\"ardoco\"],\"params\":{\"ardoco\":{\"additionalConfigs\":{\"a\":\"b\"}}}"));
        assertEquals("TASK_STATE_FAILED", done.state());
        assertEquals("INVALID_INPUT", done.error().get("code").asString());
        assertTrue(done.error().get("message").asString().contains("additionalConfigs"));
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
        assertEquals("pairwise", stub.get("scopeMode").asString());
        assertEquals(
                "whole-project",
                body.get("approaches")
                        .valueStream()
                        .filter(a -> "test-whole".equals(a.get("id").asString()))
                        .findFirst()
                        .orElseThrow()
                        .get("scopeMode")
                        .asString());
        assertTrue(body.get("approaches")
                .valueStream()
                .noneMatch(a -> "lissa".equals(a.get("id").asString())));
    }
}
