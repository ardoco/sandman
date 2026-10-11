package edu.kit.kastel.mcse.ardoco.sandman.agent;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.sun.net.httpserver.HttpServer;
import io.opentelemetry.api.trace.Span;
import io.opentelemetry.api.trace.SpanContext;
import io.opentelemetry.api.trace.TraceFlags;
import io.opentelemetry.api.trace.TraceState;
import io.opentelemetry.context.Scope;
import io.opentelemetry.sdk.OpenTelemetrySdk;
import io.opentelemetry.sdk.trace.SdkTracerProvider;
import java.io.IOException;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.atomic.AtomicInteger;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

class LlmClientTest {

    HttpServer server;
    final List<JsonNode> bodies = new CopyOnWriteArrayList<>();
    final List<String> traceparents = new CopyOnWriteArrayList<>();
    final List<String> paths = new CopyOnWriteArrayList<>();
    final AtomicInteger calls = new AtomicInteger();
    volatile int failuresBefore;
    volatile int failureStatus = 429;
    volatile String failureBody = "{}";
    volatile String model = "gpt-test";

    @BeforeEach
    void start() throws IOException {
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/", exchange -> {
            bodies.add(JsonMapper.shared().readTree(exchange.getRequestBody().readAllBytes()));
            paths.add(exchange.getRequestURI().getPath());
            traceparents.add(exchange.getRequestHeaders().getFirst("traceparent"));
            boolean fail = calls.incrementAndGet() <= failuresBefore;
            String reply = fail
                    ? failureBody
                    : exchange.getRequestURI().getPath().endsWith("/embeddings")
                            ? "{\"model\":\"" + model + "\",\"data\":[{\"embedding\":[0.5,-1.0]}],"
                                    + "\"usage\":{\"prompt_tokens\":3}}"
                            : "{\"model\":\"" + model + "\",\"choices\":[{\"message\":{\"content\":\"yes\"},"
                                    + "\"finish_reason\":\"stop\"}],\"usage\":{\"prompt_tokens\":10,\"completion_tokens\":2}}";
            byte[] bytes = reply.getBytes(StandardCharsets.UTF_8);
            exchange.sendResponseHeaders(fail ? failureStatus : 200, bytes.length);
            exchange.getResponseBody().write(bytes);
            exchange.close();
        });
        server.start();
    }

    @AfterEach
    void stop() {
        server.stop(0);
    }

    LlmClient client() {
        LlmAccess access = new LlmAccess(
                "http://127.0.0.1:" + server.getAddress().getPort() + "/v1",
                "key",
                Map.of("chat-default", "gpt-x", "embed-default", "emb-x"),
                false);
        return new LlmClient(access, "run_1", Duration.ofMillis(1), Duration.ofSeconds(5));
    }

    @Test
    void chatBodyHasModelUserSeedAndNoStream() {
        var result = client().chat("chat-default", List.of(new LlmClient.Message("user", "hi")), 0.0, 42);
        assertEquals("yes", result.content());
        JsonNode body = bodies.get(0);
        assertEquals("/v1/chat/completions", paths.get(0));
        assertEquals("gpt-x", body.get("model").asString());
        assertEquals("run_1", body.get("user").asString());
        assertEquals(42, body.get("seed").asInt());
        assertEquals(0.0, body.get("temperature").asDouble());
        assertFalse(body.has("stream"));
        assertEquals("hi", body.get("messages").get(0).get("content").asString());
    }

    @Test
    void embedSendsOneTextPerRequest() {
        float[] v = client().embed("embed-default", "text");
        assertEquals("/v1/embeddings", paths.get(0));
        assertEquals("text", bodies.get(0).get("input").asString());
        assertEquals(-1.0f, v[1]);
    }

    @Test
    void sendsTraceparentOfCurrentSpan() {
        SpanContext sc = SpanContext.create(
                "0af7651916cd43dd8448eb211c80319c",
                "b7ad6b7169203331",
                TraceFlags.getSampled(),
                TraceState.getDefault());
        Telemetry.use(OpenTelemetrySdk.builder()
                .setTracerProvider(SdkTracerProvider.builder().build())
                .setPropagators(io.opentelemetry.context.propagation.ContextPropagators.create(
                        io.opentelemetry.api.trace.propagation.W3CTraceContextPropagator.getInstance()))
                .build());
        try (Scope ignored = Span.wrap(sc).makeCurrent()) {
            client().embed("embed-default", "t");
        }
        assertTrue(traceparents.get(0).contains("0af7651916cd43dd8448eb211c80319c"));
    }

    @Test
    void status402IsBudgetExceededWithoutRetry() {
        failuresBefore = 5;
        failureStatus = 402;
        assertThrows(BudgetExceededException.class, () -> client().embed("embed-default", "t"));
        assertEquals(1, calls.get());
    }

    @Test
    void retriesTwice() {
        failuresBefore = 2;
        client().embed("embed-default", "t");
        assertEquals(3, calls.get());
    }

    @Test
    void threeRateLimitsAreUnavailable() {
        failuresBefore = 3;
        assertThrows(LlmUnavailableException.class, () -> client().embed("embed-default", "t"));
        assertEquals(3, calls.get());
    }

    @Test
    void replayMissIsCallExceptionWithBody() {
        failuresBefore = 5;
        failureStatus = 404;
        failureBody = "{\"error\":{\"code\":\"replay_miss\",\"message\":\"no recorded response for key abc\"}}";
        var e = assertThrows(LlmCallException.class, () -> client().embed("embed-default", "t"));
        assertTrue(e.getMessage().contains("key abc"));
        assertEquals(1, calls.get());
    }

    @Test
    void sumsUsageUnder16Threads() throws Exception {
        LlmClient client = client();
        ExecutorService pool = Executors.newFixedThreadPool(16);
        try {
            List<Future<?>> futures = new java.util.ArrayList<>();
            for (int i = 0; i < 64; i++) {
                futures.add(pool.submit(
                        () -> client.chat("chat-default", List.of(new LlmClient.Message("user", "q")), 0.0, 1)));
            }
            for (Future<?> f : futures) {
                f.get();
            }
        } finally {
            pool.shutdown();
        }
        assertEquals(640, client.inputTokens());
        assertEquals(128, client.outputTokens());
        assertEquals("gpt-test", client.resolvedModel());
        assertFalse(client.mixedModels());
    }

    @Test
    void mixedModelsTakeSmallestAsResolved() {
        LlmClient client = client();
        model = "b-model";
        client.embed("embed-default", "t");
        model = "a-model";
        client.embed("embed-default", "t");
        assertEquals("a-model", client.resolvedModel());
        assertTrue(client.mixedModels());
    }
}
