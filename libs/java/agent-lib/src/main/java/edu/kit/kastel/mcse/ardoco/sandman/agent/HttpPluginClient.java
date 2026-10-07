package edu.kit.kastel.mcse.ardoco.sandman.agent;

import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import tools.jackson.core.JacksonException;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

/** Calls a detector plugin via POST /v1/detect (contracts/openapi/plugin-http.yaml) with the plugin's inbound token. */
public final class HttpPluginClient {

    static final String INPUT = "skills.schema.json#/$defs/PluginDetectInput";
    static final String OUTPUT = "skills.schema.json#/$defs/PluginDetectOutput";

    /** Plugin call failed; retryable tells whether the owner should retry later. */
    public static class PluginException extends RuntimeException {
        private final boolean retryable;

        public PluginException(String message, boolean retryable, Throwable cause) {
            super(message, cause);
            this.retryable = retryable;
        }

        public boolean retryable() {
            return retryable;
        }
    }

    private final URI detectUri;
    private final String inboundToken;
    private final Contracts contracts;
    private final Duration timeout;
    private final HttpClient http =
            HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(10)).build();

    public HttpPluginClient(String baseUrl, String inboundToken, Contracts contracts) {
        this(baseUrl, inboundToken, contracts, Duration.ofMinutes(10));
    }

    public HttpPluginClient(String baseUrl, String inboundToken, Contracts contracts, Duration timeout) {
        this.detectUri = URI.create(baseUrl.replaceAll("/+$", "") + "/v1/detect");
        this.inboundToken = inboundToken;
        this.contracts = contracts;
        this.timeout = timeout;
    }

    /** Validates the input, calls the plugin and returns validated PluginDetectOutput. */
    public JsonNode detect(JsonNode input) {
        contracts.validate(INPUT, input);
        HttpRequest request = HttpRequest.newBuilder(detectUri)
                .timeout(timeout)
                .header("Authorization", "Bearer " + inboundToken)
                .header("Content-Type", "application/json")
                .POST(HttpRequest.BodyPublishers.ofByteArray(JsonMapper.shared().writeValueAsBytes(input)))
                .build();
        HttpResponse<byte[]> response;
        try {
            response = http.send(request, HttpResponse.BodyHandlers.ofByteArray());
        } catch (IOException e) {
            throw new PluginException("plugin " + detectUri + " unreachable: " + e, true, e);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new PluginException("interrupted while calling plugin " + detectUri, true, e);
        }
        if (response.statusCode() != 200) {
            String body = new String(response.body(), StandardCharsets.UTF_8);
            throw new PluginException(
                    "plugin " + detectUri + ": HTTP " + response.statusCode() + " " + body,
                    response.statusCode() == 503,
                    null);
        }
        try {
            JsonNode output = JsonMapper.shared().readTree(response.body());
            contracts.validate(OUTPUT, output);
            return output;
        } catch (JacksonException | Contracts.ContractException e) {
            throw new PluginException("plugin " + detectUri + " returned invalid output: " + e.getMessage(), false, e);
        }
    }
}
