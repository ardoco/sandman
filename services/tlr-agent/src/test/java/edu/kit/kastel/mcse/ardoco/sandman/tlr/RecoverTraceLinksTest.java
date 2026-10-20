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

    @Inject
    ApproachesRoute route;

    final List<A2aClient> clients = new ArrayList<>();

    @BeforeEach
    void seed() {
        events.events.clear();
        events.fail = false;
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
    void versionsCarryOriginAndDerivedFrom() {
        kg.write(
                "CREATE (:Artifact {id: 'art_d', projectId: $p, kind: 'architecture-model', origin: 'derived'})"
                        + "-[:HAS_VERSION]->(:ArtifactVersion {id: 'ver_d', projectId: $p, derivedFrom: 'ver_src'})",
                Map.of("p", PROJECT));
        var versions = new LinkStore(kg).versions(PROJECT, List.of("ver_d", SRC_VERSION));
        var derived = versions.stream()
                .filter(v -> v.id().equals("ver_d"))
                .findFirst()
                .orElseThrow();
        assertEquals("derived", derived.origin());
        assertEquals("ver_src", derived.derivedFrom());
        var user = versions.stream()
                .filter(v -> v.id().equals(SRC_VERSION))
                .findFirst()
                .orElseThrow();
        assertEquals("user", user.origin());
        assertEquals(null, user.derivedFrom());
    }

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
    void llmApproachRunRecordsModelUsageAndPromptIds() throws Exception {
        TestBeans.llmStatus = 200;
        var done = run(input(",\"approaches\":[\"test-llm\"]"));

        assertEquals("TASK_STATE_COMPLETED", done.state());
        var run = kg.read(
                        "MATCH (r:Run {tool: 'test-llm'}) RETURN r.status AS s, r.model AS m, r.resolvedModel AS rm, "
                                + "r.inputTokens AS i, r.outputTokens AS o, r.promptIds AS p",
                        Map.of())
                .get(0);
        assertEquals("completed", run.get("s"));
        assertEquals("chat-default", run.get("m"));
        assertEquals("gpt-test-1", run.get("rm"));
        assertEquals(10L, run.get("i"));
        assertEquals(2L, run.get("o"));
        assertEquals(List.of("test-llm@abcd1234"), run.get("p"));
    }

    @Test
    void exhaustedBudgetFailsTheRunAndTheSkillAfterTheOthersFinished() throws Exception {
        TestBeans.llmStatus = 402;
        try {
            var done = run(input(",\"approaches\":[\"test-llm\",\"" + STUB + "\"]"));

            assertEquals("TASK_STATE_FAILED", done.state());
            assertEquals("BUDGET_EXCEEDED", done.error().get("code").asString());
            assertFalse(done.error().get("retryable").asBoolean());
            assertEquals(1, activeLinks().size());
            var run = kg.read("MATCH (r:Run {tool: 'test-llm'}) RETURN r.status AS s, r.error AS e", Map.of())
                    .get(0);
            assertEquals("failed", run.get("s"));
            assertTrue(run.get("e").toString().startsWith("BUDGET_EXCEEDED"));
        } finally {
            TestBeans.llmStatus = 200;
        }
    }

    @Test
    void localOnlyProjectWithoutLocalKeyIsInvalidInputWithoutARun() throws Exception {
        kg.write("MATCH (p:Project {id: $p}) SET p.llmPolicy = 'local-only'", Map.of("p", PROJECT));
        var done = run(input(",\"approaches\":[\"test-llm\",\"" + STUB + "\"]"));

        assertEquals("TASK_STATE_FAILED", done.state());
        assertEquals("INVALID_INPUT", done.error().get("code").asString());
        assertEquals(
                0,
                kg.read("MATCH (r:Run {tool: 'test-llm'}) RETURN r.status AS s", Map.of())
                        .size());
        assertEquals(1, activeLinks().size());
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
    void decisionMentionLinksDecisionToNamedComponentAndFile() throws Exception {
        String docVersion = Ids.stableId("ver", "adr");
        String modelVersion = Ids.stableId("ver", "model");
        String decision = Ids.stableId("el", docVersion, "file");
        String component = Ids.stableId("el", modelVersion, "cm-1");
        String file = Ids.stableId("el", TGT_VERSION, "src/main/java/mini/Store.java");
        kg.write(
                "CREATE (:Artifact {id: 'art_adr', projectId: $p, kind: 'architecture-doc'})-[:HAS_VERSION]->"
                        + "(v:ArtifactVersion {id: $dv, projectId: $p})-[:HAS_ELEMENT]->(:Element:Decision "
                        + "{id: $d, projectId: $p, key: 'adr#file', kind: 'decision', name: 'Use CacheManager', "
                        + "text: 'Use CacheManager. Change src/main/java/mini/Store.java.'})",
                Map.of("p", PROJECT, "dv", docVersion, "d", decision));
        artifact(
                "architecture-model",
                modelVersion,
                Map.of(component, List.of("ModelElement", "CacheManager", "m#cm-1")));
        kg.write(
                "MATCH (v:ArtifactVersion {id: $v}) CREATE (v)-[:HAS_ELEMENT]->(:Element:CodeUnit "
                        + "{id: $id, projectId: $p, key: 'code#src/main/java/mini/Store.java', kind: 'file', "
                        + "name: 'Store.java'})",
                Map.of("v", TGT_VERSION, "id", file, "p", PROJECT));
        kg.write("MATCH (e:Element {id: $id}) SET e.kind = 'component'", Map.of("id", component));

        var in = JsonMapper.shared()
                .readTree("{\"projectId\":\"" + PROJECT + "\",\"sourceVersionIds\":[\"" + docVersion
                        + "\"],\"targetVersionIds\":[\"" + modelVersion + "\",\"" + TGT_VERSION
                        + "\"],\"approaches\":[\"decision-mention\"]}");
        var done = run(in);

        assertEquals("TASK_STATE_COMPLETED", done.state(), String.valueOf(done.error()));
        var active = kg.read(
                "MATCH (l:TraceLink {approach: 'decision-mention', active: true})-[:SOURCE]->(:Decision), "
                        + "(l)-[:TARGET]->(t:Element) RETURN t.id AS target, l.confidence AS confidence, "
                        + "l.linkType AS type ORDER BY confidence",
                Map.of());
        assertEquals(
                List.of(
                        Map.of("target", component, "confidence", 0.8, "type", "relates"),
                        Map.of("target", file, "confidence", 1.0, "type", "relates")),
                active);
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

    private HttpResponse<String> review(String linkId, String token, String actor, String body) throws Exception {
        var request = HttpRequest.newBuilder(url.resolve("/v1/links/" + linkId + "/review"))
                .header("Authorization", "Bearer " + token)
                .POST(HttpRequest.BodyPublishers.ofString(body));
        if (actor != null) {
            request.header("X-Sandman-Actor", actor);
        }
        return HttpClient.newHttpClient().send(request.build(), HttpResponse.BodyHandlers.ofString());
    }

    private HttpResponse<String> review(String linkId, String body) throws Exception {
        return review(linkId, TestBeans.TOKENS.get("query-service"), "user:alice", body);
    }

    private void link(String id, String approach, boolean active, String source, String target) {
        kg.write(
                "MATCH (s:Element {id: $s}), (t:Element {id: $t}) "
                        + "CREATE (:TraceLink {id: $id, projectId: $p, approach: $a, active: $active, linkType: 'x', "
                        + "status: 'candidate'})-[:SOURCE]->(s) "
                        + "WITH t MATCH (l:TraceLink {id: $id}) CREATE (l)-[:TARGET]->(t)",
                Map.of("id", id, "p", PROJECT, "a", approach, "active", active, "s", source, "t", target));
    }

    private Map<String, Object> linkRow(String id) {
        return kg.read("MATCH (l:TraceLink {id: $id}) RETURN properties(l) AS l", Map.of("id", id)).stream()
                .map(r -> (Map<String, Object>) r.get("l"))
                .findFirst()
                .orElseThrow();
    }

    @Test
    void reviewAppliesToElementPairAcrossApproachesButNotExplicitOrOtherPairs() throws Exception {
        link("tl_" + "1".repeat(32), "lissa", true, REQ, MEDIA);
        link("tl_" + "2".repeat(32), "stub-name-match", true, REQ, MEDIA);
        link("tl_" + "3".repeat(32), "explicit-reference", true, REQ, MEDIA);
        link("tl_" + "4".repeat(32), "lissa", true, REQ, USER);
        link("tl_" + "5".repeat(32), "lissa", false, REQ, MEDIA);
        var response = review("tl_" + "1".repeat(32), "{\"status\":\"rejected\",\"reason\":\"wrong\"}");
        assertEquals(200, response.statusCode(), response.body());
        assertEquals(
                List.of("tl_" + "1".repeat(32), "tl_" + "2".repeat(32)),
                JsonMapper.shared()
                        .readTree(response.body())
                        .get("reviewedLinkIds")
                        .valueStream()
                        .map(JsonNode::asString)
                        .toList());
        var reviewed = linkRow("tl_" + "2".repeat(32));
        assertEquals("rejected", reviewed.get("status"));
        assertEquals("user:alice", reviewed.get("reviewedBy"));
        assertEquals("wrong", reviewed.get("reviewReason"));
        assertEquals("user", reviewed.get("reviewOrigin"));
        for (String other : List.of("3", "4", "5")) {
            assertEquals("candidate", linkRow("tl_" + other.repeat(32)).get("status"));
            assertFalse(linkRow("tl_" + other.repeat(32)).containsKey("reviewedAt"));
        }
        assertEquals(1, events.events.size());
        var event = events.events.get(0);
        assertEquals("sandman.tracelink.reviewed", event.type());
        contracts.validate("events.schema.json#/$defs/TracelinkReviewed", event.data());
        assertEquals(SRC_VERSION, event.data().get("sourceVersionIds").get(0).asString());
        assertEquals(TGT_VERSION, event.data().get("targetVersionIds").get(0).asString());

        assertEquals(
                200,
                review("tl_" + "1".repeat(32), "{\"status\":\"candidate\"}").statusCode());
        var reset = linkRow("tl_" + "2".repeat(32));
        assertEquals("candidate", reset.get("status"));
        assertFalse(reset.containsKey("reviewedAt"));
        assertFalse(reset.containsKey("reviewOrigin"));
    }

    @Test
    void failedReviewEventPublishIsRetriedAndTheReviewStaysCommitted() throws Exception {
        link("tl_" + "1".repeat(32), "lissa", true, REQ, MEDIA);
        events.fail = true;
        var response = review("tl_" + "1".repeat(32), "{\"status\":\"confirmed\"}");
        assertEquals(200, response.statusCode(), response.body());
        assertEquals("confirmed", linkRow("tl_" + "1".repeat(32)).get("status"));
        assertEquals(0, events.events.size());
        assertEquals(1, new LinkStore(kg).pendingEvents().size());
        route.retryPending();
        assertEquals(0, events.events.size());
        events.fail = false;
        route.retryPending();
        assertEquals(1, events.events.size());
        assertEquals("sandman.tracelink.reviewed", events.events.get(0).type());
        assertEquals(0, new LinkStore(kg).pendingEvents().size());
    }

    @Test
    void resetClearsReviewsOnOlderLinksSoTheyAreNotCarriedOver() throws Exception {
        kg.write(
                "MATCH (s:Element {id: $s}) SET s.contentHash = 'h-src' "
                        + "WITH s MATCH (t:Element {id: $t}) SET t.contentHash = 'h-tgt'",
                Map.of("s", REQ, "t", MEDIA));
        reviewedOld("tl_old1", "x", "2026-01-01T00:00:00Z", "confirmed", "h-src");
        link("tl_" + "1".repeat(32), "lissa", true, REQ, MEDIA);
        assertEquals(
                200,
                review("tl_" + "1".repeat(32), "{\"status\":\"candidate\"}").statusCode());
        assertFalse(linkRow("tl_old1").containsKey("reviewedAt"));
        var next = activateNewLink("x");
        assertEquals("candidate", next.get("status"));
        assertFalse(next.containsKey("reviewedAt"));
    }

    @Test
    void reviewErrorsAndAuthorization() throws Exception {
        link("tl_" + "1".repeat(32), "lissa", true, REQ, MEDIA);
        link("tl_" + "2".repeat(32), "explicit-reference", true, REQ, USER);
        link("tl_" + "3".repeat(32), "lissa", false, REQ, USER);
        assertEquals(
                404,
                review("tl_" + "9".repeat(32), "{\"status\":\"confirmed\"}").statusCode());
        assertEquals(
                409,
                review("tl_" + "2".repeat(32), "{\"status\":\"confirmed\"}").statusCode());
        assertEquals(
                409,
                review("tl_" + "3".repeat(32), "{\"status\":\"confirmed\"}").statusCode());
        assertEquals(
                422, review("tl_" + "1".repeat(32), "{\"status\":\"rejected\"}").statusCode());
        assertEquals(
                422,
                review("tl_" + "1".repeat(32), "{\"status\":\"rejected\",\"reason\":\" \"}")
                        .statusCode());
        assertEquals(
                400, review("tl_" + "1".repeat(32), "{\"status\":\"maybe\"}").statusCode());
        assertEquals(
                400,
                review("tl_" + "1".repeat(32), "{\"status\":\"confirmed\",\"x\":1}")
                        .statusCode());
        assertEquals(
                400,
                review(
                                "tl_" + "1".repeat(32),
                                TestBeans.TOKENS.get("query-service"),
                                null,
                                "{\"status\":\"confirmed\"}")
                        .statusCode());
        assertEquals(
                403,
                review(
                                "tl_" + "1".repeat(32),
                                TestBeans.TOKENS.get("orchestrator"),
                                "user:alice",
                                "{\"status\":\"confirmed\"}")
                        .statusCode());
        assertEquals(
                401,
                HttpClient.newHttpClient()
                        .send(
                                HttpRequest.newBuilder(url.resolve("/v1/links/tl_" + "1".repeat(32) + "/review"))
                                        .POST(HttpRequest.BodyPublishers.ofString("{\"status\":\"confirmed\"}"))
                                        .build(),
                                HttpResponse.BodyHandlers.discarding())
                        .statusCode());
        assertEquals("candidate", linkRow("tl_" + "1".repeat(32)).get("status"));
        assertTrue(events.events.isEmpty());
    }

    private static final String OLD_SRC = "el_old_src";
    private static final String OLD_TGT = "el_old_tgt";

    private void reviewedOld(String id, String linkType, String reviewedAt, String status, String srcHash) {
        kg.write(
                "MERGE (s:Element {id: $os}) SET s.projectId = $p, s.key = 'req#REQ-1', s.contentHash = $srcHash "
                        + "MERGE (t:Element {id: $ot}) SET t.projectId = $p, t.key = 'code#MediaCache', t.contentHash = 'h-tgt' "
                        + "CREATE (l:TraceLink {id: $id, projectId: $p, approach: 'lissa', active: false, linkType: $lt, "
                        + "status: $status, reviewedBy: 'user:' + $id, reviewedAt: $at, reviewReason: 'r', "
                        + "reviewOrigin: 'user'}) CREATE (l)-[:SOURCE]->(s) CREATE (l)-[:TARGET]->(t)",
                Map.of(
                        "os",
                        OLD_SRC,
                        "ot",
                        OLD_TGT,
                        "p",
                        PROJECT,
                        "srcHash",
                        srcHash,
                        "id",
                        id,
                        "lt",
                        linkType,
                        "status",
                        status,
                        "at",
                        reviewedAt));
    }

    private Map<String, Object> activateNewLink(String linkType) {
        kg.write(
                "MATCH (s:Element {id: $s}) SET s.contentHash = 'h-src' "
                        + "WITH s MATCH (t:Element {id: $t}) SET t.contentHash = 'h-tgt' "
                        + "CREATE (r:Run {id: 'run_new', projectId: $p}) "
                        + "CREATE (l:TraceLink {id: 'tl_new', projectId: $p, approach: 'lissa', active: false, "
                        + "linkType: $lt, status: 'candidate'}) "
                        + "CREATE (l)-[:SOURCE]->(s) CREATE (l)-[:TARGET]->(t) CREATE (r)-[:PRODUCED]->(l)",
                Map.of("s", REQ, "t", MEDIA, "p", PROJECT, "lt", linkType));
        new LinkStore(kg).finish("run_new", PROJECT, "lissa", List.of(SRC_VERSION), List.of(TGT_VERSION));
        return linkRow("tl_new");
    }

    @Test
    void activationCarriesOverTheLatestReviewOfTheSameKeyAndContentHash() {
        reviewedOld("tl_old1", "x", "2026-01-01T00:00:00Z", "confirmed", "h-src");
        reviewedOld("tl_old2", "x", "2026-02-01T00:00:00Z", "rejected", "h-src");
        var carried = activateNewLink("x");
        assertEquals(true, carried.get("active"));
        assertEquals("rejected", carried.get("status"));
        assertEquals("user:tl_old2", carried.get("reviewedBy"));
        assertEquals("2026-02-01T00:00:00Z", carried.get("reviewedAt"));
        assertEquals("carried-over", carried.get("reviewOrigin"));
    }

    @Test
    void activationDoesNotCarryOverWhenContentHashDiffers() {
        reviewedOld("tl_old1", "x", "2026-01-01T00:00:00Z", "confirmed", "h-changed");
        var changed = activateNewLink("x");
        assertEquals("candidate", changed.get("status"));
        assertFalse(changed.containsKey("reviewedAt"));
    }

    @Test
    void activationDoesNotCarryOverAcrossLinkTypes() {
        reviewedOld("tl_old1", "other", "2026-01-01T00:00:00Z", "confirmed", "h-src");
        var other = activateNewLink("x");
        assertEquals("candidate", other.get("status"));
        assertFalse(other.containsKey("reviewedAt"));
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
