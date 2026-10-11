package edu.kit.kastel.mcse.ardoco.sandman.tlr.lissa;

import com.sun.net.httpserver.HttpServer;
import edu.kit.kastel.mcse.ardoco.sandman.agent.LlmAccess;
import edu.kit.kastel.mcse.ardoco.sandman.agent.LlmClient;
import java.io.IOException;
import java.io.UncheckedIOException;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.function.Function;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

/** An OpenAI-compatible stub: answers chat with a function of the request body, embeddings with a fixed vector. */
public final class FakeGateway implements AutoCloseable {

    public final List<JsonNode> chatBodies = new CopyOnWriteArrayList<>();
    public final List<JsonNode> embedBodies = new CopyOnWriteArrayList<>();
    public volatile Function<JsonNode, String> chatAnswer = body -> "yes";
    public volatile Function<JsonNode, float[]> embedAnswer = body -> new float[] {1f, 0f};
    public volatile int embedStatus = 200;
    public volatile String embedErrorBody = "{}";
    private final HttpServer server;

    public FakeGateway() {
        try {
            server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }
        server.createContext("/", exchange -> {
            JsonNode body =
                    JsonMapper.shared().readTree(exchange.getRequestBody().readAllBytes());
            String reply;
            int status = 200;
            if (exchange.getRequestURI().getPath().endsWith("/embeddings")) {
                embedBodies.add(body);
                if (embedStatus != 200) {
                    status = embedStatus;
                    reply = embedErrorBody;
                } else {
                    float[] vector = embedAnswer.apply(body);
                    StringBuilder numbers = new StringBuilder();
                    for (float v : vector) {
                        numbers.append(numbers.length() == 0 ? "" : ",").append(v);
                    }
                    reply = "{\"model\":\"emb-1\",\"data\":[{\"embedding\":[" + numbers
                            + "]}],\"usage\":{\"prompt_tokens\":3}}";
                }
            } else {
                chatBodies.add(body);
                String content = JsonMapper.shared().writeValueAsString(chatAnswer.apply(body));
                reply = "{\"model\":\"gpt-1\",\"choices\":[{\"message\":{\"content\":" + content
                        + "},\"finish_reason\":\"stop\"}],\"usage\":{\"prompt_tokens\":10,\"completion_tokens\":2}}";
            }
            byte[] bytes = reply.getBytes(StandardCharsets.UTF_8);
            exchange.sendResponseHeaders(status, bytes.length);
            exchange.getResponseBody().write(bytes);
            exchange.close();
        });
        server.start();
    }

    public LlmClient client() {
        LlmAccess access = new LlmAccess(
                "http://127.0.0.1:" + server.getAddress().getPort() + "/v1",
                "key",
                Map.of("chat-default", "gpt-x", "embed-default", "emb-x"),
                false);
        return new LlmClient(access, "run_1", Duration.ofMillis(1), Duration.ofSeconds(5));
    }

    @Override
    public void close() {
        server.stop(0);
    }
}
