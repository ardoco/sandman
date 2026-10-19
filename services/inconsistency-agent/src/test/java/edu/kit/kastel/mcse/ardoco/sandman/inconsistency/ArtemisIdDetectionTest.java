package edu.kit.kastel.mcse.ardoco.sandman.inconsistency;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.sun.net.httpserver.HttpServer;
import edu.kit.kastel.mcse.ardoco.sandman.agent.ArtifactStore;
import edu.kit.kastel.mcse.ardoco.sandman.agent.Contracts;
import edu.kit.kastel.mcse.ardoco.sandman.agent.Ids;
import edu.kit.kastel.mcse.ardoco.sandman.agent.KgClient;
import edu.kit.kastel.mcse.ardoco.sandman.agent.LlmAccess;
import edu.kit.kastel.mcse.ardoco.sandman.agent.RunRecorder;
import edu.kit.kastel.mcse.ardoco.sandman.agent.Settings;
import edu.kit.kastel.mcse.ardoco.sandman.ardocotext.SentenceRendering;
import edu.kit.kastel.mcse.ardoco.sandman.inconsistency.ardoco.ArtemisIdDetector;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;
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

/** The artemis-id detector through DetectionService on a small MediaStore KG with a stub gateway (T3.2). */
@EnabledIf("edu.kit.kastel.mcse.ardoco.sandman.inconsistency.Docker#available")
class ArtemisIdDetectionTest {

    static final String PROJECT = "ms";
    static final Path FIXTURE = Path.of("../extraction-agent/tests/fixtures/benchmark/mediastore");
    static final Path GOLDEN = Path.of("../extraction-agent/tests/golden/benchmark/mediastore");
    static final JsonMapper JSON = JsonMapper.builder().build();
    static final List<String> LINES = List.of(
            "One of the main components of Media Store is a server-side web front end, namely the Facade component, which delivers websites to the users.",
            "To meet the user authentication requirement, registration and log-in have to be offered.",
            "To this end, the Facade component delivers the corresponding registration and log-in pages to users.");
    static final String WITH_BILLING = """
            [{"name": "Facade", "type": "COMPONENT", "alternativeNames": [],
              "occurrences": ["%s", "%s"]},
             {"name": "Billing", "type": "COMPONENT", "alternativeNames": [],
              "occurrences": ["%s"]}]
            """.formatted(LINES.get(0), LINES.get(2), LINES.get(1));
    static final String FACADE_ONLY = """
            [{"name": "Facade", "type": "COMPONENT", "alternativeNames": [],
              "occurrences": ["%s", "%s"]}]
            """.formatted(LINES.get(0), LINES.get(2));

    static KgClient kg;
    static Contracts contracts;

    @TempDir
    Path tmp;

    HttpServer server;
    final List<String> paths = new CopyOnWriteArrayList<>();
    volatile String nerAnswer;
    volatile int failStatus;
    String docArt;
    ArtifactStore store;
    DetectionService service;

    @BeforeAll
    static void start() {
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
    void seed() throws Exception {
        nerAnswer = WITH_BILLING;
        failStatus = 0;
        startStub();
        TestKg.reset(kg, PROJECT);
        store = new ArtifactStore(tmp.resolve("store"));
        docArt = TestKg.artifact(kg, PROJECT, "mediastore.txt", "architecture-doc");
        String modelArt = TestKg.artifact(kg, PROJECT, "ms.repository", "architecture-model");
        kg.write("MATCH (a:Artifact {id: $a}) SET a.format = 'pcm-repository'", Map.of("a", modelArt));
        store(modelArt, Files.readAllBytes(FIXTURE.resolve("pcm/ms.repository")));
        for (int i = 0; i < LINES.size(); i++) {
            sentence(sentenceKey(i), LINES.get(i), i);
        }
        JsonNode modelGolden = JSON.readTree(Files.readString(GOLDEN.resolve("pcm/ms.repository.json")));
        for (JsonNode e : modelGolden.get("elements")) {
            if (!"file".equals(e.get("kind").asString())) {
                String key = TestKg.element(
                        kg, PROJECT, modelArt, "ModelElement", e.get("localKey").asString(), "h");
                kg.write(
                        "MATCH (e:ModelElement {key: $k}) SET e.name = $n",
                        Map.of("k", key, "n", e.get("name").asString()));
            }
        }
        var access = new LlmAccess(
                "http://127.0.0.1:" + server.getAddress().getPort(),
                "vk-test",
                Map.of("chat-default", "gpt-chat", "embed-default", "emb-model"),
                false);
        Path yaml = tmp.resolve("plugins.yaml");
        Files.writeString(yaml, "detectors:\n  artemis-id: { enabled: false }\n");
        var registry = new DetectorRegistry(List.of(new ArtemisIdDetector(policy -> access)));
        service = new DetectionService(
                kg,
                contracts,
                new RunRecorder(kg, "inconsistency-agent", "test"),
                registry,
                "http://query-service:8000",
                n -> {},
                store);
    }

    @AfterEach
    void stopStub() {
        server.stop(0);
    }

    void startStub() throws Exception {
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/", ex -> {
            String body = new String(ex.getRequestBody().readAllBytes(), StandardCharsets.UTF_8);
            paths.add(ex.getRequestURI().getPath());
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
                        + JsonMapper.shared().writeValueAsString(nerAnswer)
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

    static String sentenceKey(int index) {
        return "sec:/" + SentenceRendering.hash(LINES.get(index));
    }

    void store(String artifactId, byte[] data) throws Exception {
        var stored = store.put(PROJECT, data);
        kg.write(
                "MATCH (v:ArtifactVersion {id: $v}) SET v.storageUri = $u",
                Map.of("v", TestKg.version(artifactId), "u", stored.uri()));
    }

    void sentence(String localKey, String text, int index) {
        String version = TestKg.version(docArt);
        kg.write(
                "MATCH (v:ArtifactVersion {id: $v}) CREATE (e:Element:TextUnit {id: $id, key: $key, projectId: $p, "
                        + "kind: 'sentence', name: $name, text: $text, sentenceIndex: $i, contentHash: 'h', runId: 'run_x'}) "
                        + "CREATE (v)-[:HAS_ELEMENT]->(e)",
                Map.of(
                        "v", version,
                        "id", Ids.stableId("el", version, localKey),
                        "key", docArt + "#" + localKey,
                        "p", PROJECT,
                        "name", text.substring(0, Math.min(40, text.length())),
                        "text", text,
                        "i", index));
    }

    DetectionService.Result detect() throws Exception {
        return service.detect(new DetectionService.Request(PROJECT, null, List.of("artemis-id"), null, null, null));
    }

    Map<String, String> items() {
        Map<String, String> out = new TreeMap<>();
        kg.read(
                        "MATCH (i:Inconsistency) RETURN i.id AS id, i.fingerprint AS fp, i.type AS type, i.status AS s, i.title AS t",
                        Map.of())
                .forEach(r -> out.put(
                        (String) r.get("id"), r.get("fp") + "|" + r.get("type") + "|" + r.get("s") + "|" + r.get("t")));
        return out;
    }

    @Test
    void findingsCarryTheOneBasedSentenceKeys() throws Exception {
        var r = detect();
        assertEquals("completed", r.output().get("status").asString());
        var all = items().values();
        assertTrue(all.stream().anyMatch(v -> v.contains("|undocumented-model-element|OPEN|")));
        assertFalse(all.stream().anyMatch(v -> v.endsWith("Undocumented model element: Facade")), "Facade is named");
        var missing = kg.read(
                "MATCH (i:Inconsistency {type: 'missing-model-element'})-[:INVOLVES]->(e:Element) "
                        + "RETURN e.key AS key, i.title AS title",
                Map.of());
        assertEquals(1, missing.size());
        assertEquals(docArt + "#" + sentenceKey(1), missing.get(0).get("key"));
        assertTrue(((String) missing.get(0).get("title")).contains("Billing"));
        assertTrue(paths.stream().anyMatch(p -> p.endsWith("/embeddings")));
    }

    @Test
    void gatewayFailureIsPartialAndResolvesNothing() throws Exception {
        detect();
        var before = items();
        assertTrue(before.values().stream().anyMatch(v -> v.contains("|OPEN|")));
        failStatus = 500;
        var r = detect();
        assertEquals("partial", r.output().get("status").asString());
        assertEquals(
                "failed", r.output().get("detectorRuns").get(0).get("status").asString());
        assertEquals(before, items());
    }

    @Test
    void secondRunWithTheSameAnswersKeepsTheFingerprints() throws Exception {
        detect();
        var first = items();
        assertFalse(first.isEmpty());
        detect();
        assertEquals(first, items());
    }

    @Test
    void cleanRunResolvesAFindingThatIsGone() throws Exception {
        detect();
        assertTrue(items().values().stream().anyMatch(v -> v.contains("|missing-model-element|OPEN|")));
        nerAnswer = FACADE_ONLY;
        var r = detect();
        assertEquals("completed", r.output().get("status").asString());
        assertTrue(r.output().get("autoResolvedIds").size() >= 1);
        assertTrue(items().values().stream().anyMatch(v -> v.contains("|missing-model-element|RESOLVED|")));
        assertFalse(items().values().stream().anyMatch(v -> v.contains("|missing-model-element|OPEN|")));
    }
}
