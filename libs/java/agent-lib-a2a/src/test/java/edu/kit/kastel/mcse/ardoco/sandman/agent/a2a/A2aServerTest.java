package edu.kit.kastel.mcse.ardoco.sandman.agent.a2a;

import static edu.kit.kastel.mcse.ardoco.sandman.agent.a2a.TestAgent.OUTPUT;
import static edu.kit.kastel.mcse.ardoco.sandman.agent.a2a.TestAgent.TOKENS;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import edu.kit.kastel.mcse.ardoco.sandman.agent.Settings;
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
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import org.a2aproject.sdk.spec.A2AClientException;
import org.a2aproject.sdk.spec.A2AClientHTTPError;
import org.a2aproject.sdk.spec.DataPart;
import org.a2aproject.sdk.spec.Message;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

@QuarkusTest
class A2aServerTest {

    static final JsonNode INPUT = JsonMapper.shared()
            .readTree("{\"projectId\":\"mini\",\"sourceVersionIds\":[\"ver_" + "0".repeat(32)
                    + "\"],\"targetVersionIds\":[\"ver_" + "1".repeat(32) + "\"]}");
    static final String WF = "wf_" + "a".repeat(32);

    @TestHTTPResource("/")
    URI url;

    @Inject
    TestAgent.Recover skill;

    @Inject
    TestAgent.Events events;

    @Inject
    Settings settings;

    final List<A2aClient> clients = new ArrayList<>();

    @BeforeEach
    void reset() {
        skill.seen.clear();
        skill.output = OUTPUT;
        skill.error = null;
        skill.fatal = null;
        skill.fatalFor = null;
        skill.started = null;
        skill.release = null;
        events.events.clear();
        events.statesAtPublish.clear();
        events.fail = false;
        A2aClient probe = client(TOKENS.get("orchestrator"));
        events.stateProbe = taskId -> probe.get(agent(), taskId).state();
    }

    @AfterEach
    void close() {
        skill.fatal = null;
        skill.fatalFor = null;
        clients.forEach(A2aClient::close);
    }

    A2aClient client(String token) {
        A2aClient c = new A2aClient(token);
        clients.add(c);
        return c;
    }

    String agent() {
        return url.toString();
    }

    A2aClient.TaskSnapshot submit(A2aClient client, String actor) {
        return client.submit(
                agent(),
                "recover-trace-links",
                INPUT,
                WF,
                actor,
                UUID.randomUUID().toString());
    }

    A2aClient.TaskSnapshot await(A2aClient client, A2aClient.TaskSnapshot task) throws InterruptedException {
        for (int i = 0; i < 500 && !task.terminal(); i++) {
            Thread.sleep(10);
            task = client.get(agent(), task.taskId());
        }
        assertTrue(task.terminal(), "task did not finish: " + task);
        return task;
    }

    @Test
    void plannedSkillIsNotImplementedAndUnknownSkillIsUnknown() throws Exception {
        A2aClient client = client(TOKENS.get("orchestrator"));
        var planned = await(client, client.submit(agent(), "prune-trace-links", INPUT, null, null, "m-planned"));
        assertEquals("TASK_STATE_REJECTED", planned.state());
        assertEquals("NOT_IMPLEMENTED", planned.error().get("code").asString());
        assertFalse(planned.error().get("retryable").asBoolean());
        var unknown = await(client, client.submit(agent(), "no-such-skill", INPUT, null, null, "m-unknown"));
        assertEquals("UNKNOWN_SKILL", unknown.error().get("code").asString());
        assertTrue(events.events.isEmpty());
    }

    @Test
    void skillInvocationVectors() throws Exception {
        A2aClient client = client(TOKENS.get("orchestrator"));
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
            A2aClient.TaskSnapshot done = await(client, client.send(agent(), message));
            String name = c.get("name").asString();
            if ("TASK_STATE_REJECTED".equals(c.get("expectedState").asString())) {
                assertEquals("TASK_STATE_REJECTED", done.state(), name);
                assertEquals(
                        c.get("expectedErrorCode").asString(),
                        done.error().get("code").asString(),
                        name);
                assertFalse(done.error().get("retryable").asBoolean(), name);
            } else {
                assertTrue(
                        Set.of("TASK_STATE_SUBMITTED", "TASK_STATE_WORKING", "TASK_STATE_COMPLETED")
                                .contains(done.state()),
                        name + ": " + done);
            }
        }
    }

    @Test
    void roundTripPublishesOneEventBeforeCompletion() throws Exception {
        A2aClient client = client(TOKENS.get("orchestrator"));
        A2aClient.TaskSnapshot done =
                await(client, client.submit(agent(), "recover-trace-links", INPUT, WF, "user:alice", "m-round-trip"));
        assertEquals("TASK_STATE_COMPLETED", done.state());
        assertEquals(OUTPUT, done.result());
        assertTrue(done.result().at("/runs/0/linkCount").isIntegralNumber()); // W-4
        assertEquals(1, events.events.size());
        var event = events.events.getFirst();
        assertEquals("m-round-trip", event.causationId());
        assertEquals(WF, event.correlationId());
        assertEquals("mini", event.subject());
        assertEquals(List.of("TASK_STATE_WORKING"), events.statesAtPublish);
        assertEquals("orchestrator", skill.seen.getFirst().caller());
        assertEquals("user:alice", skill.seen.getFirst().actor());
        assertEquals("m-round-trip", skill.seen.getFirst().messageId());
        assertEquals(WF, skill.seen.getFirst().workflowId());
    }

    @Test
    void sameMessageIdRunsTheSkillOnce() throws Exception {
        A2aClient client = client(TOKENS.get("orchestrator"));
        skill.started = new CountDownLatch(1);
        skill.release = new CountDownLatch(1);
        var first = client.submit(agent(), "recover-trace-links", INPUT, WF, null, "m-dup");
        assertTrue(skill.started.await(5, TimeUnit.SECONDS));
        var second = client.submit(agent(), "recover-trace-links", INPUT, WF, null, "m-dup");
        skill.release.countDown();
        assertEquals(OUTPUT, await(client, first).result());
        assertEquals(OUTPUT, await(client, second).result());
        var third = client.submit(agent(), "recover-trace-links", INPUT, WF, null, "m-dup");
        assertEquals(OUTPUT, await(client, third).result());
        assertEquals(1, skill.seen.size());
        assertEquals(1, events.events.size());
        await(client, client.submit(agent(), "recover-trace-links", INPUT, WF, null, "m-other"));
        assertEquals(2, skill.seen.size());
    }

    @Test
    void reusedMessageIdWithDifferentInputIsRejectedWithoutRunning() throws Exception {
        A2aClient client = client(TOKENS.get("orchestrator"));
        await(client, client.submit(agent(), "recover-trace-links", INPUT, WF, null, "m-fp"));
        JsonNode other = JsonMapper.shared()
                .readTree("{\"projectId\":\"other\",\"sourceVersionIds\":[\"ver_" + "0".repeat(32)
                        + "\"],\"targetVersionIds\":[\"ver_" + "1".repeat(32) + "\"]}");
        var rejected = await(client, client.submit(agent(), "recover-trace-links", other, WF, null, "m-fp"));
        assertEquals("TASK_STATE_REJECTED", rejected.state());
        assertEquals("INVALID_INPUT", rejected.error().get("code").asString());
        assertEquals(null, rejected.result());
        assertEquals(1, skill.seen.size());
    }

    @Test
    void sameMessageIdFromDifferentCallersRunsSeparately() throws Exception {
        var first = await(
                client(TOKENS.get("orchestrator")),
                client(TOKENS.get("orchestrator")).submit(agent(), "recover-trace-links", INPUT, WF, null, "m-caller"));
        assertEquals(OUTPUT, first.result());
        skill.output = JsonMapper.shared().readTree("{\"runs\":[]}");
        A2aClient other = client(TOKENS.get("tlr-agent"));
        var second = await(other, other.submit(agent(), "recover-trace-links", INPUT, WF, null, "m-caller"));
        assertEquals(skill.output, second.result());
        assertEquals(2, skill.seen.size());
    }

    @Test
    void retryableFailureIsNotCachedButPermanentFailureIs() throws Exception {
        A2aClient client = client(TOKENS.get("orchestrator"));
        skill.error = new IllegalStateException("boom");
        await(client, client.submit(agent(), "recover-trace-links", INPUT, WF, null, "m-retry"));
        skill.error = null;
        var retried = await(client, client.submit(agent(), "recover-trace-links", INPUT, WF, null, "m-retry"));
        assertEquals(OUTPUT, retried.result());
        assertEquals(2, skill.seen.size());

        skill.error = new SkillException("BUDGET_EXCEEDED", "budget", false);
        await(client, client.submit(agent(), "recover-trace-links", INPUT, WF, null, "m-perm"));
        skill.error = null;
        var replayed = await(client, client.submit(agent(), "recover-trace-links", INPUT, WF, null, "m-perm"));
        assertEquals("BUDGET_EXCEEDED", replayed.error().get("code").asString());
        assertEquals(3, skill.seen.size());
    }

    @Test
    void anErrorOfTheSkillFailsTheTaskInsteadOfLeavingItRunning() throws Exception {
        A2aClient client = client(TOKENS.get("orchestrator"));
        skill.fatal = new UnsatisfiedLinkError("libx.so: cannot open shared object file");
        var done = await(client, submit(client, null));
        assertEquals("TASK_STATE_FAILED", done.state());
        assertEquals("INTERNAL", done.error().get("code").asString());
        assertTrue(done.error().get("retryable").asBoolean());
    }

    private int healthStatus() throws Exception {
        return HttpClient.newHttpClient()
                .send(HttpRequest.newBuilder(url.resolve("/healthz")).build(), HttpResponse.BodyHandlers.discarding())
                .statusCode();
    }

    @Test
    void heapExhaustionFailsTheTaskThenDrainsAndExitsAfterInFlightTasksFinish() throws Exception {
        List<Integer> codes = new java.util.concurrent.CopyOnWriteArrayList<>();
        CountDownLatch exited = new CountDownLatch(1);
        var original = SkillExecutor.terminator;
        SkillExecutor.terminator = code -> {
            codes.add(code);
            exited.countDown();
        };
        try {
            A2aClient client = client(TOKENS.get("orchestrator"));
            skill.started = new CountDownLatch(1);
            skill.release = new CountDownLatch(1);
            var inFlight = client.submit(agent(), "recover-trace-links", INPUT, WF, null, "m-inflight");
            assertTrue(skill.started.await(5, TimeUnit.SECONDS));
            assertEquals(200, healthStatus());

            skill.fatalFor = "m-oom";
            skill.fatal = new OutOfMemoryError("Java heap space");
            var oom = await(client, client.submit(agent(), "recover-trace-links", INPUT, WF, null, "m-oom"));
            assertEquals("TASK_STATE_FAILED", oom.state());
            assertEquals("INTERNAL", oom.error().get("code").asString());
            assertFalse(oom.error().get("retryable").asBoolean());

            var late = await(client, client.submit(agent(), "recover-trace-links", INPUT, WF, null, "m-late"));
            assertEquals("TASK_STATE_FAILED", late.state());
            assertEquals("DEPENDENCY_UNAVAILABLE", late.error().get("code").asString());
            assertTrue(late.error().get("retryable").asBoolean());
            assertEquals(503, healthStatus());
            assertFalse(exited.await(500, TimeUnit.MILLISECONDS));

            skill.release.countDown();
            assertEquals(OUTPUT, await(client, inFlight).result());
            assertTrue(exited.await(10, TimeUnit.SECONDS));
            assertEquals(List.of(SkillExecutor.VM_ERROR_EXIT_CODE), codes);
        } finally {
            SkillExecutor.terminator = original;
            SkillExecutor.resetDraining();
            skill.fatalFor = null;
        }
    }

    @Test
    void anInternalErrorAlsoDrainsAndExits() throws Exception {
        CountDownLatch exited = new CountDownLatch(1);
        var original = SkillExecutor.terminator;
        SkillExecutor.terminator = code -> exited.countDown();
        try {
            A2aClient client = client(TOKENS.get("orchestrator"));
            skill.fatal = new InternalError("jit failure");
            var done = await(client, submit(client, null));
            assertEquals("TASK_STATE_FAILED", done.state());
            assertFalse(done.error().get("retryable").asBoolean());
            assertTrue(exited.await(10, TimeUnit.SECONDS));
        } finally {
            SkillExecutor.terminator = original;
            SkillExecutor.resetDraining();
        }
    }

    @Test
    void aFailedClassInitializationFailsRetryableAndDrains() throws Exception {
        CountDownLatch exited = new CountDownLatch(1);
        var original = SkillExecutor.terminator;
        SkillExecutor.terminator = code -> exited.countDown();
        try {
            A2aClient client = client(TOKENS.get("orchestrator"));
            skill.fatal = new NoClassDefFoundError("Could not initialize class X");
            var done = await(client, submit(client, null));
            assertEquals("INTERNAL", done.error().get("code").asString());
            assertTrue(done.error().get("retryable").asBoolean());
            assertTrue(exited.await(10, TimeUnit.SECONDS));
        } finally {
            SkillExecutor.terminator = original;
            SkillExecutor.resetDraining();
        }
    }

    @Test
    void inputDrivenStackOverflowFailsTheTaskWithoutDraining() throws Exception {
        var original = SkillExecutor.terminator;
        List<Integer> codes = new java.util.concurrent.CopyOnWriteArrayList<>();
        SkillExecutor.terminator = codes::add;
        try {
            A2aClient client = client(TOKENS.get("orchestrator"));
            skill.fatal = new StackOverflowError();
            var done = await(client, submit(client, null));
            assertEquals("TASK_STATE_FAILED", done.state());
            assertEquals("INVALID_INPUT", done.error().get("code").asString());
            assertFalse(done.error().get("retryable").asBoolean());
            assertFalse(SkillExecutor.draining());
            assertEquals(200, healthStatus());
            Thread.sleep(300);
            assertEquals(List.of(), codes);
        } finally {
            SkillExecutor.terminator = original;
            SkillExecutor.resetDraining();
        }
    }

    @Test
    void invalidOutputFailsWithoutEvent() throws Exception {
        skill.output = JsonMapper.shared().readTree("{\"runs\":\"nope\"}");
        A2aClient client = client(TOKENS.get("orchestrator"));
        A2aClient.TaskSnapshot done = await(client, submit(client, null));
        assertEquals("TASK_STATE_FAILED", done.state());
        assertEquals("INTERNAL", done.error().get("code").asString());
        assertFalse(done.error().get("retryable").asBoolean());
        assertNull(done.result());
        assertTrue(events.events.isEmpty());
    }

    @Test
    void failuresAreReportedAsSkillErrors() throws Exception {
        A2aClient client = client(TOKENS.get("orchestrator"));
        skill.error = new SkillException("BUDGET_EXCEEDED", "budget", false);
        var done = await(client, submit(client, null));
        assertEquals("TASK_STATE_FAILED", done.state());
        assertEquals("BUDGET_EXCEEDED", done.error().get("code").asString());

        skill.error = new IllegalArgumentException("boom");
        done = await(client, submit(client, null));
        assertEquals("INTERNAL", done.error().get("code").asString());
        assertTrue(done.error().get("retryable").asBoolean());

        skill.error = null;
        events.fail = true;
        done = await(client, submit(client, null));
        assertEquals("TASK_STATE_FAILED", done.state());
        assertEquals("DEPENDENCY_UNAVAILABLE", done.error().get("code").asString());
        assertTrue(done.error().get("retryable").asBoolean());
        assertNull(done.result());
    }

    @Test
    void unknownAndPluginTokensAreRejected() {
        for (String token : List.of("wrong", TOKENS.get("plugin:plugin-stubs"))) {
            A2aClient client = client(token);
            var e = assertThrows(A2AClientException.class, () -> submit(client, null));
            Throwable cause = e;
            while (cause != null && !(cause instanceof A2AClientHTTPError)) {
                cause = cause.getCause();
            }
            assertEquals(401, cause instanceof A2AClientHTTPError h ? h.getCode() : -1, e.toString());
        }
        assertTrue(skill.seen.isEmpty());
        assertEquals("sandman-tlr-agent", client("wrong").card(agent()).name()); // card stays public
    }

    @Test
    void actorRejectedFromNonOrchestrator() throws Exception {
        A2aClient client = client(TOKENS.get("tlr-agent"));
        var done = await(client, submit(client, "user:mallory"));
        assertEquals("TASK_STATE_REJECTED", done.state());
        assertEquals("FORBIDDEN", done.error().get("code").asString());
        assertFalse(done.error().get("retryable").asBoolean());
        assertTrue(skill.seen.isEmpty());
    }

    @Test
    void statusReportsWorkingWhileASkillRuns() throws Exception {
        HttpClient http = HttpClient.newHttpClient();
        URI status = url.resolve("/v1/status");
        assertEquals(
                401,
                http.send(HttpRequest.newBuilder(status).build(), HttpResponse.BodyHandlers.discarding())
                        .statusCode());
        HttpRequest authorized = HttpRequest.newBuilder(status)
                .header("Authorization", "Bearer " + TOKENS.get("orchestrator"))
                .build();
        JsonNode idle = JsonMapper.shared()
                .readTree(http.send(authorized, HttpResponse.BodyHandlers.ofString())
                        .body());
        assertEquals("idle", idle.get("state").asString());
        assertEquals(0, idle.get("activeTasks").asInt());
        assertEquals("tlr-agent", idle.get("service").asString());

        skill.started = new CountDownLatch(1);
        skill.release = new CountDownLatch(1);
        A2aClient client = client(TOKENS.get("orchestrator"));
        var task = submit(client, null);
        assertTrue(skill.started.await(10, TimeUnit.SECONDS));
        JsonNode working = JsonMapper.shared()
                .readTree(http.send(authorized, HttpResponse.BodyHandlers.ofString())
                        .body());
        assertEquals("working", working.get("state").asString());
        assertEquals(1, working.get("activeTasks").asInt());
        skill.release.countDown();
        assertEquals("TASK_STATE_COMPLETED", await(client, task).state());
        JsonNode after = JsonMapper.shared()
                .readTree(http.send(authorized, HttpResponse.BodyHandlers.ofString())
                        .body());
        assertEquals("idle", after.get("state").asString());
    }

    @Test
    void cancelSetsTheFlagAndCancels() throws Exception {
        skill.started = new CountDownLatch(1);
        skill.release = new CountDownLatch(1);
        A2aClient client = client(TOKENS.get("orchestrator"));
        var task = submit(client, null);
        assertTrue(skill.started.await(10, TimeUnit.SECONDS));
        client.cancel(agent(), task.taskId());
        assertEquals("TASK_STATE_CANCELED", await(client, task).state());
        for (int i = 0; i < 100 && !skill.seen.getFirst().cancelled(); i++) {
            Thread.sleep(10);
        }
        assertTrue(skill.seen.getFirst().cancelled());
        assertTrue(events.events.isEmpty());
    }
}
