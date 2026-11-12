package edu.kit.kastel.mcse.ardoco.sandman.inconsistency.ardoco;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.sun.net.httpserver.HttpServer;
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
import org.junit.jupiter.api.io.TempDir;

/** The validation agents call the read-only code tools through the gateway: tool call first, verdict second. */
class CodeStatementToolsTest {

    private static final String VERDICT = "{\"valid\": false, \"explanation\": \"Only Java files exist.\"}";

    @TempDir
    Path code;

    private HttpServer server;
    private final List<String> bodies = new CopyOnWriteArrayList<>();

    @BeforeEach
    void startStub() throws Exception {
        Files.writeString(code.resolve("Main.java"), "class Main {}");
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/", ex -> {
            String body = new String(ex.getRequestBody().readAllBytes(), StandardCharsets.UTF_8);
            bodies.add(body);
            String message = body.contains("\"role\":\"tool\"")
                    ? "{\"role\":\"assistant\",\"content\":"
                            + tools.jackson.databind.json.JsonMapper.shared().writeValueAsString(VERDICT) + "}"
                    : "{\"role\":\"assistant\",\"content\":null,\"tool_calls\":[{\"id\":\"call_1\",\"type\":\"function\","
                            + "\"function\":{\"name\":\"findFiles\",\"arguments\":\"{\\\"glob\\\":\\\"**/*.py\\\"}\"}}]}";
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

    @AfterEach
    void stopStub() {
        server.stop(0);
    }

    private CodeStatementDetector.Validator validator(CodeStatementDetector detector) {
        var access = new LlmAccess(
                "http://127.0.0.1:" + server.getAddress().getPort(),
                "vk-test",
                Map.of("chat-default", "gpt-chat"),
                false);
        return detector.validator(new ArtemisChatModel(new LlmClient(access, "t"), access), code);
    }

    @Test
    void programmingLanguageAgentRunsToolsAndReturnsTheVerdict() {
        var verdict = validator(new ProgrammingLanguageIdDetector(p -> null))
                .validate(
                        "1:Python",
                        "Python",
                        "The service is written in Python.",
                        "The service is written in Python.",
                        code.toString());

        assertFalse(verdict.valid());
        assertEquals("Only Java files exist.", verdict.explanation());
        assertEquals(2, bodies.size(), "tool call round trip");
        assertTrue(bodies.get(0).contains("\"tools\""), "tools are sent in the OpenAI function format");
        assertTrue(bodies.get(0).contains("\"findFiles\""));
        assertTrue(bodies.get(1).contains("\"tool_call_id\":\"call_1\""));
        assertTrue(bodies.get(1).contains("\"tool_calls\""), "the assistant tool call is sent back");
    }

    @Test
    void architecturalStyleAgentRunsToolsAndReturnsTheVerdict() {
        var verdict = validator(new ArchitecturalStyleIdDetector(p -> null))
                .validate(
                        "1:Microservices",
                        "Microservices",
                        "It uses microservices.",
                        "It uses microservices.",
                        code.toString());

        assertFalse(verdict.valid());
        assertEquals(2, bodies.size());
    }
}
