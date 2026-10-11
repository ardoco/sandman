package edu.kit.kastel.mcse.ardoco.sandman.tlr;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import edu.kit.kastel.mcse.ardoco.sandman.agent.Ids;
import edu.kit.kastel.mcse.ardoco.sandman.agent.KgClient;
import edu.kit.kastel.mcse.ardoco.sandman.agent.a2a.A2aClient;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.ardoco.CorenlpProvider;
import io.quarkus.test.common.http.TestHTTPResource;
import io.quarkus.test.junit.QuarkusTest;
import jakarta.inject.Inject;
import java.net.ServerSocket;
import java.net.URI;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Map;
import java.util.UUID;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIf;
import tools.jackson.databind.json.JsonMapper;

/**
 * T2.1b: the plugin is stopped. The Run of ardocode fails, the skill fails with a retryable DEPENDENCY_UNAVAILABLE, and
 * the links of stub-name-match (which needs no plugin) are written.
 */
@EnabledIf("dockerAvailable")
@QuarkusTest
class ArdocodePluginDownTest {

    static final String PROJECT = "down";

    static boolean dockerAvailable() {
        return RecoverTraceLinksTest.dockerAvailable();
    }

    @TestHTTPResource("/")
    URI url;

    @Inject
    KgClient kg;

    @BeforeAll
    static void pluginIsStopped() throws Exception {
        CorenlpProvider.start();
        int port;
        try (var socket = new ServerSocket(0)) {
            port = socket.getLocalPort();
        }
        Path token = Files.createTempFile("code-model-token", ".txt");
        Files.writeString(token, "any");
        System.setProperty("SANDMAN_CODE_MODEL_URL", "http://127.0.0.1:" + port);
        System.setProperty("SANDMAN_CODE_MODEL_TOKEN_FILE", token.toString());
    }

    @AfterAll
    static void clearProperties() {
        System.clearProperty("SANDMAN_CODE_MODEL_URL");
        System.clearProperty("SANDMAN_CODE_MODEL_TOKEN_FILE");
    }

    @Test
    void theRunFailsRetryableAndTheOtherApproachWritesItsLinks() throws Exception {
        kg.write("MATCH (n) DETACH DELETE n", Map.of());
        kg.write("CREATE (:Project {id: $p, llmPolicy: 'cloud-allowed'})", Map.of("p", PROJECT));
        String doc = Ids.stableId("ver", "doc");
        String req = Ids.stableId("ver", "req");
        String code = Ids.stableId("ver", "code");
        version("architecture-doc", "sad.md", doc, "TextUnit", "sentence", "The MediaCache stores audio files.", 0);
        version("requirements", "req.md", req, "Requirement", "requirement", "Cache media files", null);
        version("code", "src/MediaCache.java", code, "CodeUnit", "file", "MediaCache", null);

        var client = new A2aClient(TestBeans.TOKENS.get("orchestrator"));
        try {
            var input = JsonMapper.shared()
                    .readTree("{\"projectId\":\"" + PROJECT + "\",\"sourceVersionIds\":[\"" + doc + "\",\"" + req
                            + "\"],\"targetVersionIds\":[\"" + code
                            + "\"],\"approaches\":[\"ardocode\",\"stub-name-match\"]}");
            var task = client.submit(
                    url.toString(),
                    "recover-trace-links",
                    input,
                    null,
                    null,
                    UUID.randomUUID().toString());
            for (int i = 0; i < 6000 && !task.terminal(); i++) {
                Thread.sleep(10);
                task = client.get(url.toString(), task.taskId());
            }
            assertEquals("TASK_STATE_FAILED", task.state(), String.valueOf(task));
            assertEquals("DEPENDENCY_UNAVAILABLE", task.error().get("code").asString());
            assertTrue(task.error().get("retryable").asBoolean());
        } finally {
            client.close();
        }
        var runs = kg.read("MATCH (r:Run) RETURN r.tool AS tool, r.status AS status", Map.of());
        var status = new java.util.HashMap<String, Object>();
        runs.forEach(r -> status.put((String) r.get("tool"), r.get("status")));
        assertEquals("failed", status.get("ardocode"), String.valueOf(runs));
        assertEquals("completed", status.get("stub-name-match"), String.valueOf(runs));
        assertEquals(
                0L,
                kg.read("MATCH (l:TraceLink {approach: 'ardocode'}) RETURN count(*) AS n", Map.of())
                        .get(0)
                        .get("n"));
        assertFalse(
                kg.read("MATCH (l:TraceLink {approach: 'stub-name-match', active: true}) RETURN l.id AS id", Map.of())
                        .isEmpty());
    }

    private void version(
            String kind,
            String path,
            String versionId,
            String label,
            String elementKind,
            String text,
            Integer sentence) {
        String artifact = Ids.stableId("art", path);
        kg.write(
                "CREATE (:Artifact {id: $a, projectId: $p, kind: $k, path: $path})-[:HAS_VERSION]->(v:ArtifactVersion {id: $v, projectId: $p, storageUri: 'artifact://down/"
                        + "0".repeat(64) + "'}) "
                        + "WITH v MATCH (a:Artifact {id: $a}) CREATE (a)-[:CURRENT_VERSION]->(v)",
                Map.of("a", artifact, "p", PROJECT, "k", kind, "path", path, "v", versionId));
        java.util.Map<String, Object> params = new java.util.HashMap<>();
        params.put("v", versionId);
        params.put("id", Ids.stableId("el", versionId, "e"));
        params.put("p", PROJECT);
        params.put("key", artifact + "#e");
        params.put("kind", elementKind);
        params.put("name", text);
        params.put("text", text);
        params.put("s", sentence);
        kg.write(
                "MATCH (v:ArtifactVersion {id: $v}) CREATE (v)-[:HAS_ELEMENT]->(:Element:" + label
                        + " {id: $id, projectId: $p, key: $key, kind: $kind, name: $name, text: $text, sentenceIndex: $s})",
                params);
    }
}
