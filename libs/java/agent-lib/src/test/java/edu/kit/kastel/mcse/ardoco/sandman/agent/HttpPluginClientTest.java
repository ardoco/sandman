package edu.kit.kastel.mcse.ardoco.sandman.agent;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.sun.net.httpserver.HttpServer;
import java.io.IOException;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.atomic.AtomicReference;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

class HttpPluginClientTest {

    static final JsonNode INPUT = JsonMapper.shared()
            .readTree(
                    "{\"projectId\": \"mini\", \"llmPolicy\": \"local-only\", \"queryServiceUrl\": \"http://q:8000\"}");

    HttpServer server;
    final AtomicReference<String> authorization = new AtomicReference<>();
    volatile int status;
    volatile String body;

    @BeforeEach
    void start() throws IOException {
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/v1/detect", exchange -> {
            authorization.set(exchange.getRequestHeaders().getFirst("Authorization"));
            byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
            exchange.sendResponseHeaders(status, bytes.length);
            exchange.getResponseBody().write(bytes);
            exchange.close();
        });
        server.start();
    }

    @AfterEach
    void stop() {
        server.stop(0);
    }

    HttpPluginClient client() {
        return new HttpPluginClient(
                "http://127.0.0.1:" + server.getAddress().getPort() + "/", "inbound", LibraryTest.CONTRACTS);
    }

    @Test
    void returnsValidOutputAndPresentsInboundToken() {
        status = 200;
        body = "{\"findings\": [], \"warnings\": [\"none\"]}";
        assertEquals("none", client().detect(INPUT).get("warnings").get(0).asString());
        assertEquals("Bearer inbound", authorization.get());
    }

    @Test
    void invalidOutputIsNotRetryable() {
        status = 200;
        body = "{\"unexpected\": true}";
        var e = assertThrows(HttpPluginClient.PluginException.class, () -> client().detect(INPUT));
        assertFalse(e.retryable());
    }

    @Test
    void unavailablePluginIsRetryable() {
        status = 503;
        body = "{\"title\": \"busy\"}";
        assertTrue(assertThrows(HttpPluginClient.PluginException.class, () -> client().detect(INPUT))
                .retryable());
        HttpPluginClient unreachable = client();
        server.stop(0);
        assertTrue(assertThrows(HttpPluginClient.PluginException.class, () -> unreachable.detect(INPUT))
                .retryable());
    }

    @Test
    void invalidInputIsRejectedBeforeCalling() {
        assertThrows(
                Contracts.ContractException.class,
                () -> client().detect(JsonMapper.shared().readTree("{\"projectId\": \"mini\"}")));
    }
}
