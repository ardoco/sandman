package edu.kit.kastel.mcse.ardoco.sandman.inconsistency;

import edu.kit.kastel.mcse.ardoco.sandman.agent.Contracts;
import edu.kit.kastel.mcse.ardoco.sandman.agent.HttpPluginClient;
import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import java.util.Set;
import java.util.TreeSet;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

/** Detector plugin behind plain HTTP (POST /v1/detect, GET /manifest), called with the plugin's inbound token. */
public final class PluginDetector implements InconsistencyDetector {

    private final String id;
    private final String baseUrl;
    private final String token;
    private final HttpPluginClient client;
    private final Duration timeout;
    private volatile JsonNode manifest;

    public PluginDetector(String id, String baseUrl, String token, Duration timeout, Contracts contracts) {
        this.id = id;
        this.baseUrl = baseUrl.replaceAll("/+$", "");
        this.token = token;
        this.timeout = timeout;
        this.client = new HttpPluginClient(baseUrl, token, contracts, timeout);
    }

    @Override
    public String id() {
        return id;
    }

    @Override
    public String version() {
        return manifest().path("version").asString("unknown");
    }

    @Override
    public Set<String> types() {
        Set<String> types = new TreeSet<>();
        manifest().path("types").forEach(t -> types.add(t.asString()));
        return types;
    }

    @Override
    public boolean usesLlm() {
        return manifest().path("usesLlm").asBoolean(false);
    }

    @Override
    public String kind() {
        return "plugin-http";
    }

    @Override
    public String description() {
        return manifest().hasNonNull("description")
                ? manifest().get("description").asString()
                : null;
    }

    @Override
    public List<Finding> detect(DetectionScope scope, DetectorContext ctx) {
        return detectWithWarnings(scope, ctx).findings();
    }

    @Override
    public Detection detectWithWarnings(DetectionScope scope, DetectorContext ctx) {
        ObjectNode input = JsonMapper.shared().createObjectNode();
        input.put("projectId", scope.projectId());
        input.put("llmPolicy", ctx.llmPolicy());
        if (!scope.wholeProject()) {
            input.putArray("scopeArtifactIds")
                    .addAll(scope.artifactIds().stream()
                            .sorted()
                            .map(a -> JsonMapper.shared().getNodeFactory().stringNode(a))
                            .toList());
        }
        input.put("queryServiceUrl", ctx.queryServiceUrl());
        List<Finding> findings = new ArrayList<>();
        List<String> warnings = new ArrayList<>();
        JsonNode output = client.detect(input);
        output.path("findings").forEach(f -> findings.add(Finding.fromJson(f)));
        output.path("warnings").forEach(w -> warnings.add(w.asString()));
        return new Detection(findings, warnings);
    }

    /** Manifest, cached once fetched; an unreachable plugin yields an empty one (retried on the next call). */
    private JsonNode manifest() {
        JsonNode cached = manifest;
        if (cached != null) {
            return cached;
        }
        try {
            HttpResponse<byte[]> response = HttpClient.newHttpClient()
                    .send(
                            HttpRequest.newBuilder(URI.create(baseUrl + "/manifest"))
                                    .timeout(Duration.ofSeconds(10))
                                    .header("Authorization", "Bearer " + token)
                                    .build(),
                            HttpResponse.BodyHandlers.ofByteArray());
            if (response.statusCode() == 200) {
                return manifest = JsonMapper.shared().readTree(response.body());
            }
        } catch (IOException e) {
            // fall through: listed as unknown
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
        return JsonMapper.shared().createObjectNode();
    }
}
