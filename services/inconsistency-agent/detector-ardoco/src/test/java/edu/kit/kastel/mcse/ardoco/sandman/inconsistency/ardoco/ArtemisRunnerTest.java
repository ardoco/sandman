package edu.kit.kastel.mcse.ardoco.sandman.inconsistency.ardoco;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.sun.net.httpserver.HttpServer;
import edu.kit.kastel.mcse.ardoco.core.api.models.ModelFormat;
import edu.kit.kastel.mcse.ardoco.sandman.agent.ArtifactStore;
import edu.kit.kastel.mcse.ardoco.sandman.agent.Ids;
import edu.kit.kastel.mcse.ardoco.sandman.agent.LlmAccess;
import edu.kit.kastel.mcse.ardoco.sandman.agent.LlmClient;
import edu.kit.kastel.mcse.ardoco.sandman.agent.Settings;
import edu.kit.kastel.mcse.ardoco.sandman.inconsistency.DetectionScope;
import edu.kit.kastel.mcse.ardoco.sandman.inconsistency.DetectorContext;
import edu.kit.kastel.mcse.ardoco.sandman.inconsistency.Finding;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CopyOnWriteArrayList;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import tools.jackson.dataformat.yaml.YAMLMapper;

/** Artemis on MediaStore against a stub gateway: no provider credential, no OpenAI call, no disk cache. */
class ArtemisRunnerTest {

    private static final Path MODEL =
            Path.of("../../extraction-agent/tests/fixtures/benchmark/mediastore/pcm/ms.repository");
    private static final String FACADE_ID = "_st2Y0HDrEeSqnN80MQ2uGw";
    private static final List<String> LINES = List.of(
            "One of the main components of Media Store is a server-side web front end, namely the Facade component, which delivers websites to the users.",
            "To meet the user authentication requirement, registration and log-in have to be offered.",
            "To this end, the Facade component delivers the corresponding registration and log-in pages to users.");
    private static final String NER_ANSWER = """
            [{"name": "Facade", "type": "COMPONENT", "alternativeNames": [],
              "occurrences": ["%s", "%s"]},
             {"name": "Billing", "type": "COMPONENT", "alternativeNames": [],
              "occurrences": ["%s"]}]
            """.formatted(LINES.get(0), LINES.get(2), LINES.get(1));

    private HttpServer server;
    private final List<String> auth = new CopyOnWriteArrayList<>();
    private final List<String> paths = new CopyOnWriteArrayList<>();
    private final List<String> models = new CopyOnWriteArrayList<>();
    private volatile int failStatus;

    @BeforeEach
    void startStub() throws Exception {
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/", ex -> {
            String body = new String(ex.getRequestBody().readAllBytes(), StandardCharsets.UTF_8);
            auth.add(ex.getRequestHeaders().getFirst("Authorization"));
            paths.add(ex.getRequestURI().getPath());
            models.add(body.replaceAll("(?s).*\"model\"\\s*:\\s*\"([^\"]*)\".*", "$1"));
            if (failStatus != 0) {
                ex.sendResponseHeaders(failStatus, -1);
                ex.close();
                return;
            }
            String json;
            if (ex.getRequestURI().getPath().endsWith("/embeddings")) {
                int dim = Math.floorMod(body.hashCode(), 64);
                StringBuilder v = new StringBuilder();
                for (int i = 0; i < 64; i++) {
                    v.append(i == 0 ? "" : ",").append(i == dim ? 1 : 0);
                }
                json = "{\"data\":[{\"embedding\":[" + v + "]}],\"usage\":{\"prompt_tokens\":1}}";
            } else {
                json = "{\"choices\":[{\"message\":{\"content\":"
                        + tools.jackson.databind.json.JsonMapper.shared().writeValueAsString(NER_ANSWER)
                        + "},\"finish_reason\":\"stop\"}],\"model\":\"stub\",\"usage\":{\"prompt_tokens\":2,\"completion_tokens\":3}}";
            }
            byte[] out = json.getBytes(StandardCharsets.UTF_8);
            ex.getResponseHeaders().add("Content-Type", "application/json");
            ex.sendResponseHeaders(200, out.length);
            ex.getResponseBody().write(out);
            ex.close();
        });
        server.start();
    }

    @AfterEach
    void stopStub() {
        server.stop(0);
    }

    private LlmAccess access() {
        return new LlmAccess(
                "http://127.0.0.1:" + server.getAddress().getPort(),
                "vk-test",
                Map.of("chat-default", "gpt-chat", "embed-default", "emb-model"),
                false);
    }

    @Test
    void findingsComeFromTheGatewayAnswers() {
        var access = access();
        var client = new LlmClient(access, "t");
        var raw = IdRunner.extract(ArtemisRunner.run(LINES, MODEL, ModelFormat.PCM, client, access));

        assertFalse(raw.models().stream().anyMatch(m -> m.modelId().equals(FACADE_ID)), "Facade is documented");
        assertTrue(raw.models().size() > 3);
        assertEquals(1, raw.words().size());
        assertEquals("Billing", raw.words().get(0).word());
        assertEquals(2, raw.words().get(0).sentence(), "NAER lines are 1-based, as Mapping expects");
    }

    @Test
    void allTrafficGoesToTheGatewayWithTheVirtualKey() throws Exception {
        var access = access();
        ArtemisRunner.run(LINES, MODEL, ModelFormat.PCM, new LlmClient(access, "t"), access);

        assertFalse(paths.isEmpty());
        assertTrue(paths.stream().anyMatch(p -> p.endsWith("/chat/completions")));
        assertTrue(paths.stream().anyMatch(p -> p.endsWith("/embeddings")), "embeddings use the gateway");
        assertTrue(auth.stream().allMatch("Bearer vk-test"::equals));
        assertTrue(models.stream().allMatch(m -> m.equals("gpt-chat") || m.equals("emb-model")), models.toString());
        assertFalse(Files.exists(Path.of(".cache-llm")), "no disk cache");
    }

    @Test
    void localOnlyUsesTheLocalKeyAndLocalRoutes() {
        var access = new LlmAccess(
                "http://127.0.0.1:" + server.getAddress().getPort(),
                "vk-local",
                Map.of("chat-default", "gpt-chat", "chat-local", "local-chat", "embed-local", "local-emb"),
                true);
        ArtemisRunner.run(LINES, MODEL, ModelFormat.PCM, new LlmClient(access, "t"), access);

        assertFalse(paths.isEmpty());
        assertTrue(auth.stream().allMatch("Bearer vk-local"::equals), auth.toString());
        assertTrue(models.stream().allMatch(m -> m.equals("local-chat") || m.equals("local-emb")), models.toString());
        assertTrue(models.contains("local-emb"));
    }

    @Test
    void missingLocalKeyFailsTheRun(@TempDir Path tmp) throws Exception {
        Path modelsFile = tmp.resolve("models.yaml");
        Files.writeString(modelsFile, "routes: {chat-local: local-chat, embed-local: local-emb}\n");
        Files.writeString(tmp.resolve("cloud"), "cloud-key");
        Map<String, String> env = Map.of(
                "SANDMAN_SERVICE_NAME",
                "inconsistency-agent",
                "SANDMAN_LLM_BASE_URL",
                "http://127.0.0.1:1",
                "SANDMAN_LLM_MODELS_FILE",
                modelsFile.toString(),
                "SANDMAN_LLM_KEY_FILE",
                tmp.resolve("cloud").toString());
        var detector = new ArtemisIdDetector(policy -> LlmAccess.forPolicy(Settings.fromEnv(env), policy));
        var ctx = new DetectorContext(
                null, LlmAccess.LOCAL_ONLY, "http://q", Map.of(), new ArtifactStore(tmp.resolve("store")), () -> false);
        var scope = new DetectionScope("p", null, true);

        assertThrows(LlmAccess.PolicyException.class, () -> detector.detectWithWarnings(scope, ctx));
        assertTrue(paths.isEmpty());
    }

    @Test
    void gatewayErrorFailsTheRun() {
        failStatus = 500;
        var access = access();
        var client = new LlmClient(access, "t", Duration.ofMillis(1), Duration.ofSeconds(5));

        assertThrows(RuntimeException.class, () -> ArtemisRunner.run(LINES, MODEL, ModelFormat.PCM, client, access));
    }

    @Test
    void sameAnswersGiveSameFingerprints() {
        var access = access();
        var client = new LlmClient(access, "t");
        var first = fingerprints(client, access);
        var second = fingerprints(client, access);

        assertFalse(first.isEmpty());
        assertEquals(first, second);
    }

    private List<String> fingerprints(LlmClient client, LlmAccess access) {
        var raw = IdRunner.extract(ArtemisRunner.run(LINES, MODEL, ModelFormat.PCM, client, access));
        var sentences =
                List.of(new Mapping.Sentence("d#s1"), new Mapping.Sentence("d#s2"), new Mapping.Sentence("d#s3"));
        Map<String, Mapping.ModelEl> byId = new java.util.HashMap<>();
        raw.models().forEach(m -> byId.put(m.modelId(), new Mapping.ModelEl("m#" + m.modelId(), m.name(), false)));
        return Mapping.map(List.of(new Mapping.RunResult("d", "m", sentences, byId, raw))).stream()
                .map(f -> Ids.fingerprint(
                        f.type(),
                        ArtemisIdDetector.ID,
                        f.involved().stream().map(Finding.Involved::elementKey).toList()))
                .toList();
    }

    @Test
    void artemisIdStaysDisabledInTheShippedConfig() throws Exception {
        var node = new YAMLMapper().readTree(Files.readString(Path.of("../../../config/inconsistency/plugins.yaml")));
        var entry = node.path("detectors").path("artemis-id");
        assertFalse(entry.isMissingNode());
        assertFalse(entry.path("enabled").asBoolean(true));
    }

    @Test
    void terminatesLinesForNaer() {
        assertEquals("No dot.", ArtemisRunner.terminated("No dot"));
        assertEquals("Has dot.", ArtemisRunner.terminated("Has dot."));
        assertEquals("Really?", ArtemisRunner.terminated("Really?"));
    }

    @Test
    void declaresTheContract() {
        var d = new ArtemisIdDetector();
        assertEquals("artemis-id", d.id());
        assertEquals(java.util.Set.of("textKinds", "modelKinds"), d.paramKeys());
        assertTrue(d.usesLlm());
        assertEquals(
                edu.kit.kastel.mcse.ardoco.sandman.inconsistency.InconsistencyDetector.ScopeMode.WHOLE_PROJECT,
                d.scopeMode());
    }
}
