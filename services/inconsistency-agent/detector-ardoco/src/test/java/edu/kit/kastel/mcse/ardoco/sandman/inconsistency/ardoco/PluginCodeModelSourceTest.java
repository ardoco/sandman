package edu.kit.kastel.mcse.ardoco.sandman.inconsistency.ardoco;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import edu.kit.kastel.mcse.ardoco.sandman.agent.a2a.A2aClient;
import edu.kit.kastel.mcse.ardoco.sandman.inconsistency.ardoco.ArtemisIdDetector.CodeModelSource.CodeFile;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

class PluginCodeModelSourceTest {

    private static final JsonNode ACM = JsonMapper.shared().readTree("{\"codeItemRepository\":{\"a\":1}}");
    private static final List<CodeFile> FILES = List.of(new CodeFile("src/A.java", "/artifacts/a"));

    private static A2aClient.TaskSnapshot task(String state, JsonNode result, JsonNode error) {
        return new A2aClient.TaskSnapshot("t1", state, result, error);
    }

    private static PluginCodeModelSource source(Duration timeout, PluginCodeModelSource.Calls calls) {
        return new PluginCodeModelSource("http://plugin", timeout, Duration.ofMillis(1), calls);
    }

    @Test
    void pollsUntilTheTaskIsDoneAndWritesTheAcm(@TempDir Path dir) throws IOException {
        List<JsonNode> inputs = new ArrayList<>();
        int[] gets = {0};
        var source = source(Duration.ofSeconds(5), new PluginCodeModelSource.Calls() {
            public A2aClient.TaskSnapshot submit(String url, JsonNode input, String id) {
                inputs.add(input);
                return task("TASK_STATE_WORKING", null, null);
            }

            public A2aClient.TaskSnapshot get(String url, String taskId) {
                return ++gets[0] < 3
                        ? task("TASK_STATE_WORKING", null, null)
                        : task(
                                "TASK_STATE_COMPLETED",
                                JsonMapper.shared().createObjectNode().set("acm", ACM),
                                null);
            }
        });
        Path file = source.acm("p1", FILES, dir);
        assertEquals(dir.resolve("codeModel.acm"), file);
        assertEquals(ACM, JsonMapper.shared().readTree(Files.readString(file)));
        assertEquals("p1", inputs.get(0).get("projectId").asString());
        assertEquals(
                "/artifacts/a",
                inputs.get(0).get("currentArtifacts").get(0).get("storagePath").asString());
        assertEquals(3, gets[0]);
    }

    @Test
    void aPluginErrorIsAnIoException(@TempDir Path dir) {
        var error = JsonMapper.shared().readTree("{\"code\":\"FORBIDDEN\",\"message\":\"no\"}");
        var source = source(Duration.ofSeconds(5), new PluginCodeModelSource.Calls() {
            public A2aClient.TaskSnapshot submit(String url, JsonNode input, String id) {
                return task("TASK_STATE_FAILED", null, error);
            }

            public A2aClient.TaskSnapshot get(String url, String taskId) {
                throw new AssertionError();
            }
        });
        var e = assertThrows(IOException.class, () -> source.acm("p1", FILES, dir));
        assertTrue(e.getMessage().contains("FORBIDDEN"), e.getMessage());
    }

    @Test
    void aDownPluginAndATimeoutAreIoExceptions(@TempDir Path dir) {
        var down = source(Duration.ofSeconds(5), new PluginCodeModelSource.Calls() {
            public A2aClient.TaskSnapshot submit(String url, JsonNode input, String id) {
                throw new IllegalStateException("connection refused");
            }

            public A2aClient.TaskSnapshot get(String url, String taskId) {
                throw new AssertionError();
            }
        });
        assertTrue(assertThrows(IOException.class, () -> down.acm("p1", FILES, dir))
                .getMessage()
                .contains("unavailable"));
        var slow = source(Duration.ofMillis(5), new PluginCodeModelSource.Calls() {
            public A2aClient.TaskSnapshot submit(String url, JsonNode input, String id) {
                return task("TASK_STATE_WORKING", null, null);
            }

            public A2aClient.TaskSnapshot get(String url, String taskId) {
                return task("TASK_STATE_WORKING", null, null);
            }
        });
        assertTrue(assertThrows(IOException.class, () -> slow.acm("p1", FILES, dir))
                .getMessage()
                .contains("did not answer"));
    }
}
