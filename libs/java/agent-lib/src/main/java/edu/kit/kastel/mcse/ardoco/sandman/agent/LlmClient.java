package edu.kit.kastel.mcse.ardoco.sandman.agent;

import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.List;
import java.util.concurrent.ConcurrentSkipListSet;
import java.util.concurrent.atomic.AtomicLong;
import tools.jackson.core.JacksonException;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ArrayNode;
import tools.jackson.databind.node.ObjectNode;

/**
 * Non-streaming chat and embedding calls to the gateway or llm-replay (OpenAI-compatible API, JDK HttpClient). One
 * client per Run: it sums the token usage and collects the response models. Thread-safe.
 */
public final class LlmClient {

    public record Message(String role, String content) {}

    public record ChatResult(String content, String finishReason, String model) {}

    private static final int RETRIES = 2;

    private final LlmAccess access;
    private final String runId;
    private final Duration backoff;
    private final Duration timeout;
    private final HttpClient http =
            HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(10)).build();
    private final AtomicLong inputTokens = new AtomicLong();
    private final AtomicLong outputTokens = new AtomicLong();
    private final ConcurrentSkipListSet<String> models = new ConcurrentSkipListSet<>();

    public LlmClient(LlmAccess access, String runId) {
        this(access, runId, Duration.ofMillis(500), Duration.ofMinutes(5));
    }

    public LlmClient(LlmAccess access, String runId, Duration backoff, Duration timeout) {
        this.access = access;
        this.runId = runId;
        this.backoff = backoff;
        this.timeout = timeout;
    }

    /** One chat completion on a logical route; seed may be null. */
    public ChatResult chat(String route, List<Message> messages, double temperature, Integer seed) {
        ObjectNode body = body(route);
        ArrayNode array = body.putArray("messages");
        messages.forEach(m -> array.addObject().put("role", m.role()).put("content", m.content()));
        body.put("temperature", temperature);
        if (seed != null) {
            body.put("seed", seed);
        }
        JsonNode response = post("/chat/completions", body);
        JsonNode choice = response.path("choices").path(0);
        return new ChatResult(
                choice.path("message").path("content").asString(""),
                choice.path("finish_reason").asString(""),
                response.path("model").asString(""));
    }

    /** One embedding of one text; one request per text, so a cassette key depends on one text only. */
    public float[] embed(String route, String text) {
        ObjectNode body = body(route);
        body.put("input", text);
        JsonNode vector = post("/embeddings", body).path("data").path(0).path("embedding");
        float[] out = new float[vector.size()];
        for (int i = 0; i < out.length; i++) {
            out[i] = (float) vector.get(i).asDouble();
        }
        return out;
    }

    public long inputTokens() {
        return inputTokens.get();
    }

    public long outputTokens() {
        return outputTokens.get();
    }

    /** The smallest response model by string order, or null before the first answer. */
    public String resolvedModel() {
        return models.isEmpty() ? null : models.first();
    }

    public boolean mixedModels() {
        return models.size() > 1;
    }

    private ObjectNode body(String route) {
        ObjectNode body = JsonMapper.shared().createObjectNode();
        body.put("model", access.model(route));
        body.put("user", runId);
        return body;
    }

    private JsonNode post(String path, ObjectNode body) {
        URI uri = URI.create(access.baseUrl().replaceAll("/+$", "") + path);
        byte[] bytes = JsonMapper.shared().writeValueAsBytes(body);
        RuntimeException last = null;
        for (int attempt = 0; attempt <= RETRIES; attempt++) {
            if (attempt > 0) {
                sleep(backoff.multipliedBy(1L << (attempt - 1)));
            }
            HttpRequest.Builder builder = HttpRequest.newBuilder(uri)
                    .timeout(timeout)
                    .header("Authorization", "Bearer " + access.apiKey())
                    .header("Content-Type", "application/json");
            String traceparent = Telemetry.traceparent();
            if (traceparent != null) {
                builder.header("traceparent", traceparent);
            }
            HttpResponse<byte[]> response;
            try {
                response = http.send(
                        builder.POST(HttpRequest.BodyPublishers.ofByteArray(bytes))
                                .build(),
                        HttpResponse.BodyHandlers.ofByteArray());
            } catch (IOException e) {
                last = new LlmUnavailableException("LLM endpoint " + uri + " unreachable: " + e, e);
                continue;
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                throw new LlmUnavailableException("interrupted", e);
            }
            int status = response.statusCode();
            String text = new String(response.body(), StandardCharsets.UTF_8);
            if (status == 402) {
                throw new BudgetExceededException("LLM budget exhausted: " + text);
            }
            if (status == 429 || status >= 500) {
                last = new LlmUnavailableException("LLM endpoint " + uri + " answered " + status + ": " + text, null);
                continue;
            }
            if (status >= 400) {
                throw new LlmCallException("LLM call " + path + " failed with " + status + ": " + text);
            }
            return parse(path, text);
        }
        throw last;
    }

    private JsonNode parse(String path, String text) {
        JsonNode json;
        try {
            json = JsonMapper.shared().readTree(text);
        } catch (JacksonException e) {
            throw new LlmCallException("LLM call " + path + " returned no JSON: " + e.getOriginalMessage());
        }
        JsonNode usage = json.path("usage");
        inputTokens.addAndGet(usage.path("prompt_tokens").asLong(0));
        outputTokens.addAndGet(usage.path("completion_tokens").asLong(0));
        String model = json.path("model").asString("");
        if (!model.isEmpty()) {
            models.add(model);
        }
        return json;
    }

    private static void sleep(Duration d) {
        try {
            Thread.sleep(d);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new LlmUnavailableException("interrupted", e);
        }
    }
}
