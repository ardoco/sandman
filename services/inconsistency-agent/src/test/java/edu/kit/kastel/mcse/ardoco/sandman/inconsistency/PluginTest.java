package edu.kit.kastel.mcse.ardoco.sandman.inconsistency;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.sun.net.httpserver.HttpServer;
import edu.kit.kastel.mcse.ardoco.sandman.agent.Contracts;
import edu.kit.kastel.mcse.ardoco.sandman.agent.Ids;
import edu.kit.kastel.mcse.ardoco.sandman.agent.KgClient;
import edu.kit.kastel.mcse.ardoco.sandman.agent.RunRecorder;
import edu.kit.kastel.mcse.ardoco.sandman.agent.Settings;
import java.io.IOException;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CopyOnWriteArrayList;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIf;
import org.junit.jupiter.api.io.TempDir;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

@EnabledIf("edu.kit.kastel.mcse.ardoco.sandman.inconsistency.Docker#available")
class PluginTest {

    static final String TOKEN = "plugin-inbound-token";
    static final String PROJECT = "mini";
    static KgClient kg;
    static Contracts contracts;

    HttpServer server;
    final List<String> authHeaders = new CopyOnWriteArrayList<>();
    final List<JsonNode> inputs = new CopyOnWriteArrayList<>();
    volatile String response;
    String reqArt;
    String req1;
    DetectionService service;

    @BeforeAll
    static void connect() {
        kg = TestKg.client();
        contracts = new Contracts(Settings.defaultContractsDir());
    }

    @AfterAll
    static void close() {
        if (kg != null) {
            kg.close();
        }
    }

    @BeforeEach
    void start() throws IOException {
        TestKg.reset(kg, PROJECT);
        reqArt = TestKg.artifact(kg, PROJECT, "requirements.md", "requirements");
        req1 = TestKg.element(kg, PROJECT, reqArt, "Requirement", "REQ-1", "h1");
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext(
                "/manifest",
                ex -> reply(
                        ex,
                        200,
                        "{\"id\":\"fake-plugin\",\"version\":\"9\",\"operations\":[\"detect\"],\"types\":[\"plug-type\"],\"usesLlm\":false}"));
        server.createContext("/v1/detect", ex -> {
            String auth = ex.getRequestHeaders().getFirst("Authorization");
            authHeaders.add(String.valueOf(auth));
            if (!("Bearer " + TOKEN).equals(auth)) {
                reply(ex, 401, "{}");
                return;
            }
            inputs.add(JsonMapper.shared().readTree(ex.getRequestBody().readAllBytes()));
            reply(ex, 200, response);
        });
        server.start();
        String url = "http://127.0.0.1:" + server.getAddress().getPort();
        service(TOKEN, url);
    }

    void service(String token, String url) {
        var plugin = new PluginDetector("fake-plugin", url, token, Duration.ofSeconds(5), contracts);
        service = new DetectionService(
                kg,
                contracts,
                new RunRecorder(kg, "inconsistency-agent", "test"),
                new DetectorRegistry(List.of(plugin)),
                "http://query-service:8000",
                n -> {});
    }

    @AfterEach
    void stop() {
        server.stop(0);
    }

    static void reply(com.sun.net.httpserver.HttpExchange ex, int status, String body) throws IOException {
        byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
        ex.getResponseHeaders().add("Content-Type", "application/json");
        ex.sendResponseHeaders(status, bytes.length);
        ex.getResponseBody().write(bytes);
        ex.close();
    }

    DetectionService.Result detect(List<String> scope) throws Exception {
        return service.detect(new DetectionService.Request(PROJECT, scope, null, null, null, null));
    }

    @Test
    void findingsOfAPluginGoThroughTheLifecycle() throws Exception {
        response =
                "{\"findings\":[{\"type\":\"plug-type\",\"severity\":\"error\",\"title\":\"t\",\"involved\":[{\"elementKey\":\""
                        + req1 + "\",\"role\":\"subject\"}],\"evidence\":{\"why\":\"because\"}}]}";
        var r = detect(null);
        String id = Ids.stableId("inc", PROJECT, Ids.fingerprint("plug-type", "fake-plugin", List.of(req1)));
        assertEquals(id, r.output().get("newIds").get(0).asString());
        assertEquals(List.of("Bearer " + TOKEN), authHeaders);
        JsonNode input = inputs.get(0);
        assertEquals("local-only", input.get("llmPolicy").asString());
        assertEquals("http://query-service:8000", input.get("queryServiceUrl").asString());
        assertFalse(input.has("scopeArtifactIds"));
        var row = kg.read(
                        "MATCH (i:Inconsistency {id: $id}) RETURN i.detector AS d, i.detectorVersion AS v",
                        Map.of("id", id))
                .get(0);
        assertEquals("fake-plugin", row.get("d"));
        assertEquals("9", row.get("v"));

        detect(List.of(reqArt));
        assertEquals(
                List.of(reqArt),
                List.of(inputs.get(1).get("scopeArtifactIds").get(0).asString()));
    }

    @Test
    void pluginWarningsAreReportedInDetectorRuns() throws Exception {
        response = "{\"findings\":[],\"warnings\":[\"skipped input a\",\"slow\"]}";
        var run = detect(null).output().get("detectorRuns").get(0);
        assertEquals("completed", run.get("status").asString());
        assertEquals(
                List.of("skipped input a", "slow"),
                run.get("warnings").valueStream().map(JsonNode::asString).toList());
        response = "{\"findings\":[]}";
        assertFalse(detect(null).output().get("detectorRuns").get(0).has("warnings"));
    }

    @Test
    void invalidPluginOutputFailsTheDetectorRunWithoutCrash() throws Exception {
        response = "{\"findings\":[{\"type\":\"Bad Type\"}]}";
        var r = detect(null);
        assertEquals("partial", r.output().get("status").asString());
        var run = r.output().get("detectorRuns").get(0);
        assertEquals("failed", run.get("status").asString());
        assertTrue(
                run.get("error").asString().contains("invalid output"),
                run.get("error").asString());
        assertEquals(0, r.output().get("newIds").size());
    }

    @Test
    void wrongInboundTokenFailsTheDetectorRun() throws Exception {
        service("wrong-token", "http://127.0.0.1:" + server.getAddress().getPort());
        response = "{\"findings\":[]}";
        var run = detect(null).output().get("detectorRuns").get(0);
        assertEquals("failed", run.get("status").asString());
        assertTrue(run.get("error").asString().contains("401"));
    }

    @Test
    void registryRejectsSchemaInvalidConfig(@TempDir Path dir) throws Exception {
        Path yaml = dir.resolve("plugins.yaml");
        Files.writeString(
                yaml, "plugins:\n  - { id: p, transport: carrier-pigeon, url: 'http://x', tokenFile: '/t' }\n");
        var e = assertThrows(Settings.ConfigException.class, () -> DetectorRegistry.load(yaml, contracts));
        assertTrue(e.getMessage().contains("invalid config " + yaml), e.getMessage());
    }

    @Test
    void registryRejectsA2aPlugins(@TempDir Path dir) throws Exception {
        Path yaml = dir.resolve("plugins.yaml");
        Files.writeString(yaml, "plugins:\n  - { id: p, transport: a2a, url: 'http://x', tokenFile: '/nope' }\n");
        var e = assertThrows(Settings.ConfigException.class, () -> DetectorRegistry.load(yaml, contracts));
        assertTrue(e.getMessage().contains("http only"));
        Path token = dir.resolve("token");
        Files.writeString(token, TOKEN + "\n");
        Files.writeString(
                yaml,
                "detectors:\n  stub-untraced: { enabled: true }\nplugins:\n  - { id: p, transport: http, url: 'http://x', tokenFile: '"
                        + token + "', timeout: 30s }\n");
        assertEquals(
                List.of("stub-untraced", "p"),
                DetectorRegistry.load(yaml, contracts).all().stream()
                        .map(InconsistencyDetector::id)
                        .toList());
    }
}
