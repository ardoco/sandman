package edu.kit.kastel.mcse.ardoco.sandman.tlr.ardoco;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import edu.kit.kastel.mcse.ardoco.sandman.agent.a2a.A2aClient.TaskSnapshot;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.ApproachUnavailableException;
import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.CancellationException;
import java.util.function.Supplier;
import org.junit.jupiter.api.Test;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

class CodeModelClientTest {

    private static final List<CodeModelClient.File> FILES =
            List.of(new CodeModelClient.File("a/A.java", "/artifacts/x"));

    /** Scripted plugin: the snapshots to answer, in order; the last one repeats. */
    private static class Script implements CodeModelClient.Calls {
        final List<TaskSnapshot> answers;
        final List<String> calls = new ArrayList<>();
        JsonNode submitted;
        RuntimeException submitFails;

        Script(TaskSnapshot... answers) {
            this.answers = List.of(answers);
        }

        @Override
        public String identity(String url) {
            return "code-extractor-ardoco@2.1.0+c1";
        }

        @Override
        public TaskSnapshot submit(String url, JsonNode input, String messageId) {
            calls.add("submit");
            submitted = input;
            if (submitFails != null) {
                throw submitFails;
            }
            return next();
        }

        @Override
        public TaskSnapshot get(String url, String taskId) {
            calls.add("get");
            return next();
        }

        @Override
        public TaskSnapshot cancel(String url, String taskId) {
            calls.add("cancel");
            return next();
        }

        private TaskSnapshot next() {
            int done = (int) calls.stream().filter(c -> !c.equals("cancel")).count();
            return answers.get(Math.min(done, answers.size()) - 1);
        }
    }

    private static TaskSnapshot working() {
        return new TaskSnapshot("t1", "TASK_STATE_WORKING", null, null);
    }

    private static TaskSnapshot completed() {
        return new TaskSnapshot("t1", "TASK_STATE_COMPLETED", json("{\"acm\":{\"codeItemRepository\":{}}}"), null);
    }

    private static TaskSnapshot failed(String state, String code, boolean retryable) {
        return new TaskSnapshot(
                "t1",
                state,
                null,
                json("{\"code\":\"" + code + "\",\"message\":\"m\",\"retryable\":" + retryable + "}"));
    }

    private static JsonNode json(String text) {
        return JsonMapper.shared().readTree(text);
    }

    private static CodeModelClient client(Script script, Duration timeout) {
        return new CodeModelClient("http://plugin", timeout, Duration.ofMillis(1), script);
    }

    private static JsonNode acm(Script script) {
        return client(script, Duration.ofSeconds(5)).acm("p", FILES, () -> false);
    }

    @Test
    void sendsTheCurrentFilesAndPollsUntilTheAcmIsThere() {
        var script = new Script(working(), working(), completed());
        assertTrue(acm(script).has("codeItemRepository"));
        assertEquals(List.of("submit", "get", "get"), script.calls);
        assertEquals("p", script.submitted.get("projectId").asString());
        assertEquals(
                "/artifacts/x",
                script.submitted.at("/currentArtifacts/0/storagePath").asString());
    }

    @Test
    void unavailablePluginAndRetryableErrorsAreRetryableFailuresOfTheApproach() {
        for (TaskSnapshot s : List.of(
                failed("TASK_STATE_FAILED", "DEPENDENCY_UNAVAILABLE", true),
                failed("TASK_STATE_FAILED", "INTERNAL", true))) {
            assertThrows(ApproachUnavailableException.class, () -> acm(new Script(s)));
        }
        var down = new Script(working());
        down.submitFails = new IllegalStateException("connection refused");
        assertThrows(ApproachUnavailableException.class, () -> acm(down));
    }

    @Test
    void capsAndOtherErrorsFailTheRunWithoutRetry() {
        for (TaskSnapshot s : List.of(
                failed("TASK_STATE_FAILED", "INVALID_INPUT", false),
                failed("TASK_STATE_REJECTED", "INVALID_INPUT", false),
                failed("TASK_STATE_FAILED", "INTERNAL", false),
                failed("TASK_STATE_FAILED", "FORBIDDEN", false))) {
            var e = assertThrows(IllegalStateException.class, () -> acm(new Script(s)));
            assertTrue(e.getMessage().contains("m"), e.getMessage());
        }
        var noAcm = new TaskSnapshot("t1", "TASK_STATE_COMPLETED", json("{}"), null);
        assertThrows(IllegalStateException.class, () -> acm(new Script(noAcm)));
    }

    @Test
    void aTimeoutCancelsTheTaskAndIsRetryable() {
        var script = new Script(working());
        assertThrows(
                ApproachUnavailableException.class,
                () -> client(script, Duration.ofMillis(20)).acm("p", FILES, () -> false));
        assertEquals("cancel", script.calls.getLast());
    }

    @Test
    void cancellationCancelsTheTask() {
        var script = new Script(working());
        Supplier<Boolean> after = new Supplier<>() {
            int n;

            @Override
            public Boolean get() {
                return ++n > 2;
            }
        };
        assertThrows(
                CancellationException.class,
                () -> client(script, Duration.ofSeconds(5)).acm("p", FILES, after::get));
        assertEquals("cancel", script.calls.getLast());
    }

    @Test
    void identityIsPluginNameAndVersionAndAnUnreachableCardIsUnavailable() {
        assertEquals(
                "code-extractor-ardoco@2.1.0+c1",
                client(new Script(working()), Duration.ZERO).identity());
        var broken = new CodeModelClient("http://plugin", Duration.ZERO, Duration.ZERO, new Script(working()) {
            @Override
            public String identity(String url) {
                throw new IllegalStateException("no route");
            }
        });
        assertThrows(ApproachUnavailableException.class, broken::identity);
    }

    @Test
    void timeoutSettingTakesSecondsOrASuffix() {
        assertEquals(Duration.ofMinutes(10), CodeModelClient.duration("10m"));
        assertEquals(Duration.ofSeconds(90), CodeModelClient.duration("90s"));
        assertEquals(Duration.ofSeconds(90), CodeModelClient.duration("90"));
    }
}
