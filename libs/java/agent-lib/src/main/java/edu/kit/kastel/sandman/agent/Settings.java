package edu.kit.kastel.sandman.agent;

import java.io.IOException;
import java.lang.reflect.RecordComponent;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Arrays;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.function.Function;
import java.util.stream.Collectors;
import tools.jackson.core.JacksonException;
import tools.jackson.core.type.TypeReference;
import tools.jackson.databind.json.JsonMapper;

/** Runtime configuration of one service from the SANDMAN_* environment (Stage 2 §1.7); *_FILE values are read from files. */
public record Settings(
        String serviceName,
        Path contractsDir,
        String llmBaseUrl,
        String llmKey,
        String llmLocalKey,
        Path llmModelsFile,
        String kgUri,
        String kgUser,
        String kgPassword,
        String natsUrl,
        String serviceToken,
        Map<String, String> serviceVerify, // caller -> sha256(token)
        String pluginInboundToken,
        Path artifactStore,
        Path configDir,
        Path logFile) {

    /** Missing or invalid configuration. */
    public static class ConfigException extends RuntimeException {
        public ConfigException(String message) {
            super(message);
        }

        public ConfigException(String message, Throwable cause) {
            super(message, cause);
        }
    }

    public static Settings fromEnv() {
        return fromEnv(System.getenv());
    }

    /** Reads settings; fails if SANDMAN_SERVICE_NAME is absent. */
    public static Settings fromEnv(Map<String, String> env) {
        String name = plain(env, "SANDMAN_SERVICE_NAME");
        if (name == null) {
            throw new ConfigException("SANDMAN_SERVICE_NAME is required");
        }
        Path verifyFile = path(env, "SANDMAN_SERVICE_VERIFY_FILE");
        Map<String, String> verify = Map.of();
        if (verifyFile != null) {
            try {
                verify = Map.copyOf(
                        JsonMapper.shared().readValue(Files.readString(verifyFile), new TypeReference<>() {}));
            } catch (IOException | JacksonException e) {
                throw new ConfigException("cannot read SANDMAN_SERVICE_VERIFY_FILE=" + verifyFile + ": " + e, e);
            }
        }
        Path contracts = path(env, "SANDMAN_CONTRACTS_DIR");
        return new Settings(
                name,
                contracts != null ? contracts : defaultContractsDir(),
                plain(env, "SANDMAN_LLM_BASE_URL"),
                secret(env, "SANDMAN_LLM_KEY"),
                secret(env, "SANDMAN_LLM_LOCAL_KEY"),
                path(env, "SANDMAN_LLM_MODELS_FILE"),
                plain(env, "SANDMAN_KG_URI"),
                plain(env, "SANDMAN_KG_USER"),
                secret(env, "SANDMAN_KG_PASSWORD"),
                plain(env, "SANDMAN_NATS_URL"),
                secret(env, "SANDMAN_SERVICE_TOKEN"),
                verify,
                secret(env, "SANDMAN_PLUGIN_INBOUND_TOKEN"),
                path(env, "SANDMAN_ARTIFACT_STORE"),
                path(env, "SANDMAN_CONFIG_DIR"),
                path(env, "SANDMAN_LOG_FILE"));
    }

    /** The repository's contracts/ directory, searched upwards from the working directory. */
    public static Path defaultContractsDir() {
        for (Path dir = Path.of("").toAbsolutePath(); dir != null; dir = dir.getParent()) {
            if (Files.isRegularFile(dir.resolve("contracts/common.schema.json"))) {
                return dir.resolve("contracts");
            }
        }
        return Path.of("contracts").toAbsolutePath();
    }

    /** Fails with one message listing every required setting (component names, e.g. "kgUri") that is unset. */
    public void require(String... names) {
        Map<String, RecordComponent> components = Arrays.stream(Settings.class.getRecordComponents())
                .collect(Collectors.toMap(RecordComponent::getName, Function.identity()));
        List<String> unknown =
                Arrays.stream(names).filter(n -> !components.containsKey(n)).toList();
        if (!unknown.isEmpty()) {
            throw new IllegalArgumentException("unknown settings: " + unknown);
        }
        String missing = Arrays.stream(names)
                .filter(n -> isUnset(components.get(n)))
                .map(Settings::envName)
                .collect(Collectors.joining(", "));
        if (!missing.isEmpty()) {
            throw new ConfigException(serviceName + ": missing configuration " + missing);
        }
    }

    private boolean isUnset(RecordComponent component) {
        try {
            Object value = component.getAccessor().invoke(this);
            return value == null || "".equals(value) || (value instanceof Map<?, ?> m && m.isEmpty());
        } catch (ReflectiveOperationException e) {
            throw new IllegalStateException(e);
        }
    }

    static String envName(String component) {
        return "SANDMAN_" + component.replaceAll("([A-Z])", "_$1").toUpperCase(Locale.ROOT);
    }

    private static String plain(Map<String, String> env, String name) {
        String value = env.get(name);
        return value == null || value.isEmpty() ? null : value;
    }

    private static Path path(Map<String, String> env, String name) {
        String value = plain(env, name);
        return value == null ? null : Path.of(value);
    }

    private static String secret(Map<String, String> env, String name) {
        String file = env.get(name + "_FILE");
        if (file == null) {
            return plain(env, name);
        }
        try {
            return Files.readString(Path.of(file)).strip();
        } catch (IOException e) {
            throw new ConfigException("cannot read " + name + "_FILE=" + file + ": " + e, e);
        }
    }

    @Override
    public String toString() {
        return "Settings[serviceName=" + serviceName + "]"; // never print secrets
    }
}
