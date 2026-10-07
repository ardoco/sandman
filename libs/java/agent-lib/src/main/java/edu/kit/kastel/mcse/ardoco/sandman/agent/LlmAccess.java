package edu.kit.kastel.mcse.ardoco.sandman.agent;

import java.util.LinkedHashMap;
import java.util.Map;
import tools.jackson.databind.JsonNode;
import tools.jackson.dataformat.yaml.YAMLMapper;

/** Base URL, virtual key and gateway models for a project's LLM policy (models.yaml); configures LangChain4j in adapters. */
public record LlmAccess(String baseUrl, String apiKey, Map<String, String> routes, boolean localOnly) {

    public static final String LOCAL_ONLY = "local-only";

    /** A route is not allowed under the project's LLM policy. */
    public static class PolicyException extends RuntimeException {
        public PolicyException(String message) {
            super(message);
        }
    }

    /** Cloud key and default routes, or the local key and '-local' routes for local-only projects. */
    public static LlmAccess forPolicy(Settings settings, String llmPolicy) {
        settings.require("llmBaseUrl", "llmModelsFile");
        Map<String, String> routes = new LinkedHashMap<>();
        JsonNode yaml = new YAMLMapper().readTree(settings.llmModelsFile());
        if (yaml != null) {
            yaml.path("routes")
                    .properties()
                    .forEach(e -> routes.put(e.getKey(), e.getValue().asString()));
        }
        if (LOCAL_ONLY.equals(llmPolicy)) {
            if (settings.llmLocalKey() == null) {
                throw new PolicyException("local-only project but no local virtual key configured");
            }
            return new LlmAccess(settings.llmBaseUrl(), settings.llmLocalKey(), Map.copyOf(routes), true);
        }
        settings.require("llmKey");
        return new LlmAccess(settings.llmBaseUrl(), settings.llmKey(), Map.copyOf(routes), false);
    }

    public String chatRoute() {
        return localOnly ? "chat-local" : "chat-default";
    }

    public String embedRoute() {
        return localOnly ? "embed-local" : "embed-default";
    }

    /** Gateway model name of a logical route; local-only projects may use '-local' routes only. */
    public String model(String route) {
        if (localOnly && !route.endsWith("-local")) {
            throw new PolicyException("route '" + route + "' is not allowed for local-only projects");
        }
        String model = routes.get(route);
        if (model == null) {
            throw new PolicyException("unknown route '" + route + "'");
        }
        return model;
    }

    public String chatModel() {
        return model(chatRoute());
    }

    public String embedModel() {
        return model(embedRoute());
    }

    @Override
    public String toString() {
        return "LlmAccess[baseUrl=" + baseUrl + ", localOnly=" + localOnly + "]"; // never print the key
    }
}
