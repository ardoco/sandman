package edu.kit.kastel.mcse.ardoco.sandman.inconsistency;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import edu.kit.kastel.mcse.ardoco.sandman.agent.Contracts;
import edu.kit.kastel.mcse.ardoco.sandman.agent.Ids;
import edu.kit.kastel.mcse.ardoco.sandman.agent.KgClient;
import edu.kit.kastel.mcse.ardoco.sandman.agent.a2a.A2aClient;
import io.quarkus.test.common.http.TestHTTPResource;
import io.quarkus.test.junit.QuarkusTest;
import jakarta.inject.Inject;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.util.List;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIf;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

@EnabledIf("edu.kit.kastel.mcse.ardoco.sandman.inconsistency.Docker#available")
@QuarkusTest
class AgentTest {

    static final String PROJECT = "mini";
    static final String QS = TestBeans.TOKENS.get("query-service");
    static final String ORCH = TestBeans.TOKENS.get("orchestrator");

    @TestHTTPResource("/")
    URI url;

    @Inject
    KgClient kg;

    @Inject
    Contracts contracts;

    @Inject
    TestBeans.Events events;

    String reqArt;
    String req1;
    String id1;
    final HttpClient http = HttpClient.newHttpClient();

    @BeforeEach
    void seed() throws Exception {
        events.events.clear();
        TestKg.reset(kg, PROJECT);
        reqArt = TestKg.artifact(kg, PROJECT, "requirements.md", "requirements");
        req1 = TestKg.element(kg, PROJECT, reqArt, "Requirement", "REQ-1", "h1");
        TestKg.element(kg, PROJECT, reqArt, "Requirement", "REQ-2", "h2");
        try (A2aClient client = new A2aClient(ORCH)) {
            var task = client.submit(
                    url.toString(),
                    "detect-inconsistencies",
                    JsonMapper.shared().readTree("{\"projectId\":\"mini\"}"),
                    null,
                    null,
                    "msg-seed");
            for (int i = 0; i < 500 && !task.terminal(); i++) {
                Thread.sleep(20);
                task = client.get(url.toString(), task.taskId());
            }
            assertEquals("TASK_STATE_COMPLETED", task.state(), String.valueOf(task.error()));
            contracts.validate("skills.schema.json#/$defs/DetectInconsistenciesOutput", task.result());
            assertEquals(2, task.result().get("newIds").size());
        }
        assertEquals(1, events.events.size());
        assertEquals("sandman.inconsistencies.updated", events.events.get(0).type());
        assertEquals("msg-seed", events.events.get(0).causationId());
        id1 = Ids.stableId("inc", PROJECT, Ids.fingerprint("untraced-requirement", "stub-untraced", List.of(req1)));
    }

    record Response(int status, JsonNode body) {}

    Response call(String method, String path, String token, String actor, String body) throws Exception {
        var b = HttpRequest.newBuilder(url.resolve(path));
        if (token != null) {
            b.header("Authorization", "Bearer " + token);
        }
        if (actor != null) {
            b.header("X-Sandman-Actor", actor);
        }
        b.header("Content-Type", "application/json");
        b.method(
                method, body == null ? HttpRequest.BodyPublishers.noBody() : HttpRequest.BodyPublishers.ofString(body));
        HttpResponse<String> r = http.send(b.build(), HttpResponse.BodyHandlers.ofString());
        return new Response(r.statusCode(), JsonMapper.shared().readTree(r.body()));
    }

    Response transition(String id, String token, String actor, String body) throws Exception {
        return call("POST", "/v1/inconsistencies/" + id + "/transitions", token, actor, body);
    }

    @Test
    void detectorsAreListed() throws Exception {
        Response r = call("GET", "/v1/detectors", QS, null, null);
        assertEquals(200, r.status());
        contracts.validate("skills.schema.json#/$defs/ListDetectorsOutput", r.body());
        assertEquals("stub-untraced", r.body().get("detectors").get(0).get("id").asString());
    }

    @Test
    void authenticationAndPluginCallersAreChecked() throws Exception {
        assertEquals(401, call("GET", "/v1/detectors", null, null, null).status());
        assertEquals(401, call("GET", "/v1/detectors", "bogus", null, null).status());
        assertEquals(
                403,
                call("GET", "/v1/detectors", TestBeans.TOKENS.get("plugin:p"), null, null)
                        .status());
    }

    @Test
    void listFiltersCountsAndPaging() throws Exception {
        Response r = call("GET", "/v1/projects/mini/inconsistencies?limit=1", QS, null, null);
        assertEquals(200, r.status());
        assertEquals(1, r.body().get("items").size());
        assertEquals(2, r.body().get("counts").get("OPEN").asInt());
        assertEquals(0, r.body().get("counts").get("RESOLVED").asInt());
        String cursor = r.body().get("nextCursor").asString();
        Response second = call("GET", "/v1/projects/mini/inconsistencies?limit=1&cursor=" + cursor, QS, null, null);
        assertEquals(1, second.body().get("items").size());
        assertTrue(!second.body().has("nextCursor"));
        assertEquals(
                "requirements.md",
                r.body().get("items").get(0).get("artifactPaths").get(0).asString());
        assertEquals(
                0,
                call("GET", "/v1/projects/mini/inconsistencies?status=ACCEPTED,DEFERRED", QS, null, null)
                        .body()
                        .get("items")
                        .size());
        assertEquals(
                2,
                call(
                                "GET",
                                "/v1/projects/mini/inconsistencies?status=OPEN&type=untraced-requirement&severity=warning&artifactId="
                                        + reqArt,
                                QS,
                                null,
                                null)
                        .body()
                        .get("items")
                        .size());
        assertEquals(
                0,
                call("GET", "/v1/projects/mini/inconsistencies?changedSince=2999-01-01T00:00:00Z", QS, null, null)
                        .body()
                        .get("items")
                        .size());
        assertEquals(
                400,
                call("GET", "/v1/projects/mini/inconsistencies?status=NOPE", QS, null, null)
                        .status());
    }

    @Test
    void detailShowsInvolvedElementsHistoryAndAllowedTransitions() throws Exception {
        Response r = call("GET", "/v1/inconsistencies/" + id1, QS, null, null);
        assertEquals(200, r.status());
        JsonNode body = r.body();
        assertEquals("OPEN", body.get("status").asString());
        JsonNode element = body.get("involved").get(0).get("element");
        assertEquals(req1, element.get("key").asString());
        assertEquals("Requirement", element.get("label").asString());
        assertEquals("requirements.md", element.get("artifactPath").asString());
        assertEquals(1, body.get("history").size()); // creation entry
        assertEquals(List.of("DEFERRED", "ACCEPTED", "REJECTED", "RESOLVED"), strings(body.get("allowedTransitions")));
        assertEquals(
                404,
                call("GET", "/v1/inconsistencies/inc_" + "0".repeat(32), QS, null, null)
                        .status());
    }

    @Test
    void userTransitionIsAppliedAndPublished() throws Exception {
        Response r = transition(
                id1, QS, "user:alice", "{\"to\":\"ACCEPTED\",\"expectedStatus\":\"OPEN\",\"reason\":\"intended\"}");
        assertEquals(200, r.status(), r.body().toString());
        assertEquals("ACCEPTED", r.body().get("status").asString());
        assertFalse(r.body().get("history").get(0).has("from")); // creation entry
        assertEquals("OPEN", r.body().get("history").get(0).get("to").asString());
        assertEquals("user:alice", r.body().get("history").get(1).get("actor").asString());
        assertEquals("intended", r.body().get("history").get(1).get("reason").asString());
        assertEquals(List.of("OPEN"), strings(r.body().get("allowedTransitions")));
        var event = events.events.getLast();
        assertEquals("sandman.inconsistency.status.changed", event.type());
        assertEquals("ACCEPTED", event.data().get("to").asString());
        Response resolved = transition(id1, QS, "user:alice", "{\"to\":\"OPEN\",\"expectedStatus\":\"ACCEPTED\"}");
        assertEquals("OPEN", resolved.body().get("status").asString());
    }

    @Test
    void actorHeaderFromANonForwarderIsForbidden() throws Exception {
        assertEquals(
                403,
                transition(id1, ORCH, "user:alice", "{\"to\":\"RESOLVED\",\"expectedStatus\":\"OPEN\"}")
                        .status());
        assertEquals(
                "OPEN",
                call("GET", "/v1/inconsistencies/" + id1, QS, null, null)
                        .body()
                        .get("status")
                        .asString());
    }

    @Test
    void userTransitionErrorPaths() throws Exception {
        assertEquals(
                422,
                transition(id1, QS, "user:alice", "{\"to\":\"ACCEPTED\",\"expectedStatus\":\"OPEN\"}")
                        .status());
        assertEquals(
                422,
                transition(id1, QS, "user:alice", "{\"to\":\"REOPENED\",\"expectedStatus\":\"OPEN\"}")
                        .status());
        assertEquals(
                409,
                transition(
                                id1,
                                QS,
                                "user:alice",
                                "{\"to\":\"ACCEPTED\",\"expectedStatus\":\"DEFERRED\",\"reason\":\"x\"}")
                        .status());
        assertEquals(
                403,
                transition(id1, ORCH, "user:alice", "{\"to\":\"RESOLVED\",\"expectedStatus\":\"OPEN\"}")
                        .status());
        assertEquals(
                400,
                transition(id1, QS, null, "{\"to\":\"RESOLVED\",\"expectedStatus\":\"OPEN\"}")
                        .status());
        assertEquals(
                400,
                transition(id1, QS, "alice", "{\"to\":\"RESOLVED\",\"expectedStatus\":\"OPEN\"}")
                        .status());
        assertEquals(
                400, transition(id1, QS, "user:alice", "{\"to\":\"RESOLVED\"}").status());
        assertEquals(
                400,
                transition(id1, QS, "user:alice", "{\"to\":\"RESOLVED\",\"expectedStatus\":\"OPEN\",\"x\":1}")
                        .status());
        assertEquals(
                404,
                transition(
                                "inc_" + "0".repeat(32),
                                QS,
                                "user:alice",
                                "{\"to\":\"RESOLVED\",\"expectedStatus\":\"OPEN\"}")
                        .status());
        assertEquals(401, transition(id1, null, "user:alice", "{}").status());
        Response problem = transition(id1, QS, "user:alice", "{\"to\":\"REOPENED\",\"expectedStatus\":\"OPEN\"}");
        assertEquals(422, problem.body().get("status").asInt());
        assertEquals(
                "OPEN",
                call("GET", "/v1/inconsistencies/" + id1, QS, null, null)
                        .body()
                        .get("status")
                        .asString());
        assertEquals(1, events.events.size()); // only the detection event
    }

    static List<String> strings(JsonNode array) {
        return array.valueStream().map(JsonNode::asString).toList();
    }
}
