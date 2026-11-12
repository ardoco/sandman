package edu.kit.kastel.mcse.ardoco.sandman.inconsistency;

import static org.junit.jupiter.api.Assertions.assertEquals;
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
import edu.kit.kastel.mcse.ardoco.sandman.inconsistency.ardoco.ArchitecturalStyleIdDetector;
import edu.kit.kastel.mcse.ardoco.sandman.inconsistency.ardoco.ProgrammingLanguageIdDetector;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.nio.file.Path;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIf;
import org.junit.jupiter.api.io.TempDir;
import tools.jackson.databind.json.JsonMapper;

/** The statement-level detectors through DetectionService with a stub gateway: findings point at a sentence, not at a model element (T3.9). */
@EnabledIf("edu.kit.kastel.mcse.ardoco.sandman.inconsistency.Docker#available")
class CodeStatementDetectionTest {

    static final String PROJECT = "st";
    static final List<String> LINES = List.of(
            "The service is written in Python.",
            "It follows the microservices architecture.",
            "Users log in on the web page.");
    static final String VERDICT = "{\"valid\": false, \"explanation\": \"The code contradicts the statement.\"}";

    static KgClient kg;
    static Contracts contracts;

    @TempDir
    Path tmp;

    HttpServer server;
    volatile String nerAnswer;
    String docArt;
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
        startStub();
        TestKg.reset(kg, PROJECT);
        var store = new ArtifactStore(tmp.resolve("store"));
        docArt = TestKg.artifact(kg, PROJECT, "doc.txt", "architecture-doc");
        String codeArt = TestKg.artifact(kg, PROJECT, "src/Main.java", "code");
        var stored = store.put(PROJECT, "class Main {}".getBytes(StandardCharsets.UTF_8));
        kg.write(
                "MATCH (v:ArtifactVersion {id: $v}) SET v.storageUri = $u",
                Map.of("v", TestKg.version(codeArt), "u", stored.uri()));
        for (int i = 0; i < LINES.size(); i++) {
            String version = TestKg.version(docArt);
            String localKey = "sec:/" + SentenceRendering.hash(LINES.get(i));
            kg.write(
                    "MATCH (v:ArtifactVersion {id: $v}) CREATE (e:Element:TextUnit {id: $id, key: $key, projectId: $p, "
                            + "kind: 'sentence', name: $name, text: $text, sentenceIndex: $i, contentHash: 'h', runId: 'run_x'}) "
                            + "CREATE (v)-[:HAS_ELEMENT]->(e)",
                    Map.of(
                            "v",
                            version,
                            "id",
                            Ids.stableId("el", version, localKey),
                            "key",
                            docArt + "#" + localKey,
                            "p",
                            PROJECT,
                            "name",
                            LINES.get(i),
                            "text",
                            LINES.get(i),
                            "i",
                            i));
        }
        var access = new LlmAccess(
                "http://127.0.0.1:" + server.getAddress().getPort(),
                "vk-test",
                Map.of("chat-default", "gpt-chat"),
                false);
        var registry = new DetectorRegistry(List.of(
                new ProgrammingLanguageIdDetector(policy -> access),
                new ArchitecturalStyleIdDetector(policy -> access)));
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
            String message;
            if (!body.contains("\"tools\"")) {
                message = "{\"role\":\"assistant\",\"content\":"
                        + JsonMapper.shared().writeValueAsString(nerAnswer) + "}";
            } else if (body.contains("\"role\":\"tool\"")) {
                message = "{\"role\":\"assistant\",\"content\":"
                        + JsonMapper.shared().writeValueAsString(VERDICT) + "}";
            } else {
                message =
                        "{\"role\":\"assistant\",\"content\":null,\"tool_calls\":[{\"id\":\"call_1\",\"type\":\"function\","
                                + "\"function\":{\"name\":\"findFiles\",\"arguments\":\"{\\\"glob\\\":\\\"**/*.py\\\"}\"}}]}";
            }
            byte[] out = ("{\"choices\":[{\"message\":" + message
                            + ",\"finish_reason\":\"stop\"}],\"model\":\"stub\",\"usage\":{\"prompt_tokens\":2,\"completion_tokens\":3}}")
                    .getBytes(StandardCharsets.UTF_8);
            ex.getResponseHeaders().add("Content-Type", "application/json");
            ex.sendResponseHeaders(200, out.length);
            ex.getResponseBody().write(out);
            ex.close();
        });
        server.start();
    }

    DetectionService.Result detect(String detector) throws Exception {
        return service.detect(new DetectionService.Request(PROJECT, null, List.of(detector), null, null, null));
    }

    @Test
    void programmingLanguageFindingPointsAtTheSentence() throws Exception {
        nerAnswer =
                "[{\"name\": \"Python\", \"type\": \"PROGRAMMING_LANGUAGE\", \"alternativeNames\": [], \"occurrences\": [\""
                        + LINES.get(0) + "\"]}]";
        var r = detect(ProgrammingLanguageIdDetector.ID);

        assertEquals(
                "completed", r.output().get("status").asString(), r.output().toString());
        var rows = kg.read(
                "MATCH (i:Inconsistency {type: 'programming-language-mismatch'})-[:INVOLVES]->(e:Element) "
                        + "RETURN e.key AS key, i.title AS title, i.evidence AS evidence",
                Map.of());
        assertEquals(1, rows.size());
        assertEquals(
                docArt + "#sec:/" + SentenceRendering.hash(LINES.get(0)),
                rows.get(0).get("key"));
        assertTrue(((String) rows.get(0).get("title")).contains("Python"));
        assertTrue(((String) rows.get(0).get("evidence")).contains("The code contradicts the statement."));
    }

    @Test
    void architecturalStyleFindingPointsAtTheSentence() throws Exception {
        nerAnswer =
                "[{\"name\": \"microservices\", \"type\": \"ARCHITECTURAL_PATTERN\", \"alternativeNames\": [], \"occurrences\": [\""
                        + LINES.get(1) + "\"]}]";
        var r = detect(ArchitecturalStyleIdDetector.ID);

        assertEquals(
                "completed", r.output().get("status").asString(), r.output().toString());
        var rows = kg.read(
                "MATCH (i:Inconsistency {type: 'architectural-style-mismatch'})-[:INVOLVES]->(e:Element) RETURN e.key AS key",
                Map.of());
        assertEquals(1, rows.size());
        assertEquals(
                docArt + "#sec:/" + SentenceRendering.hash(LINES.get(1)),
                rows.get(0).get("key"));
    }
}
