package edu.kit.kastel.mcse.ardoco.sandman.inconsistency;

import edu.kit.kastel.mcse.ardoco.sandman.agent.Contracts;
import edu.kit.kastel.mcse.ardoco.sandman.agent.Settings.ConfigException;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.ServiceLoader;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

/** Enabled detectors: ServiceLoader detectors switched on and HTTP plugins registered in config/inconsistency/plugins.yaml. */
public final class DetectorRegistry {

    private static final JsonMapper JSON = JsonMapper.shared();
    private static final Pattern DURATION = Pattern.compile("(\\d+)(ms|s|m|h)");

    private final Map<String, InconsistencyDetector> detectors = new LinkedHashMap<>();
    private final Map<String, Map<String, Object>> params = new LinkedHashMap<>();

    public DetectorRegistry(List<InconsistencyDetector> enabled) {
        enabled.forEach(d -> detectors.put(d.id(), d));
    }

    /** Fails on unknown enabled detectors, a2a plugins and unreadable token files. */
    public static DetectorRegistry load(Path pluginsYaml, Contracts contracts) {
        JsonNode config = contracts.loadConfig("inconsistency-plugins", pluginsYaml);
        Map<String, InconsistencyDetector> available = new LinkedHashMap<>();
        ServiceLoader.load(InconsistencyDetector.class).forEach(d -> available.put(d.id(), d));
        List<InconsistencyDetector> enabled = new java.util.ArrayList<>();
        Map<String, Map<String, Object>> params = new LinkedHashMap<>();
        config.path("detectors").properties().forEach(e -> {
            if (e.getValue().path("enabled").asBoolean(false)) {
                InconsistencyDetector d = available.get(e.getKey());
                if (d == null) {
                    throw new ConfigException("unknown detector '" + e.getKey() + "' in " + pluginsYaml);
                }
                JsonNode given = e.getValue().path("params");
                Map<String, Object> values = new LinkedHashMap<>();
                given.properties().forEach(p -> {
                    if (!d.paramKeys().contains(p.getKey())) {
                        throw new ConfigException("detector '" + d.id() + "': unknown param '" + p.getKey() + "' in "
                                + pluginsYaml + " (known: " + new java.util.TreeSet<>(d.paramKeys()) + ")");
                    }
                    values.put(p.getKey(), JSON.treeToValue(p.getValue(), Object.class));
                });
                params.put(d.id(), values);
                enabled.add(d);
            }
        });
        for (JsonNode p : config.path("plugins")) {
            String id = p.path("id").asString();
            if (!"http".equals(p.path("transport").asString())) {
                throw new ConfigException("plugin '" + id + "': transport '"
                        + p.path("transport").asString("") + "' not supported (http only)");
            }
            try {
                enabled.add(new PluginDetector(
                        id,
                        p.path("url").asString(),
                        Files.readString(Path.of(p.path("tokenFile").asString()))
                                .strip(),
                        duration(p.path("timeout").asString("10m")),
                        contracts));
            } catch (IOException e) {
                throw new ConfigException("plugin '" + id + "': cannot read tokenFile: " + e, e);
            }
        }
        DetectorRegistry registry = new DetectorRegistry(enabled);
        registry.params.putAll(params);
        return registry;
    }

    static Duration duration(String text) {
        Matcher m = DURATION.matcher(text);
        if (!m.matches()) {
            throw new ConfigException("invalid duration '" + text + "'");
        }
        long n = Long.parseLong(m.group(1));
        return switch (m.group(2)) {
            case "ms" -> Duration.ofMillis(n);
            case "s" -> Duration.ofSeconds(n);
            case "m" -> Duration.ofMinutes(n);
            default -> Duration.ofHours(n);
        };
    }

    public List<InconsistencyDetector> all() {
        return List.copyOf(detectors.values());
    }

    public Map<String, Object> params(String id) {
        return params.getOrDefault(id, Map.of());
    }

    public InconsistencyDetector get(String id) {
        return detectors.get(id);
    }
}
