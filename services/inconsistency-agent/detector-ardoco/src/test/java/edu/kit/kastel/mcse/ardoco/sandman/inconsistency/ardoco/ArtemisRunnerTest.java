package edu.kit.kastel.mcse.ardoco.sandman.inconsistency.ardoco;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.sun.net.httpserver.HttpServer;
import edu.kit.kastel.mcse.ardoco.core.api.models.ModelFormat;
import edu.kit.kastel.mcse.ardoco.sandman.agent.LlmAccess;
import edu.kit.kastel.mcse.ardoco.sandman.agent.LlmClient;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CopyOnWriteArrayList;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

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

    @BeforeEach
    void startStub() throws Exception {
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/", ex -> {
            String body = new String(ex.getRequestBody().readAllBytes(), StandardCharsets.UTF_8);
            auth.add(ex.getRequestHeaders().getFirst("Authorization"));
            paths.add(ex.getRequestURI().getPath());
            models.add(body.replaceAll("(?s).*\"model\"\\s*:\\s*\"([^\"]*)\".*", "$1"));
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
