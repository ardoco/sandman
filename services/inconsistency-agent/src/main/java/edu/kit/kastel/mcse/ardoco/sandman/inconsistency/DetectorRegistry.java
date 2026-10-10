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

/** Enabled detectors: ServiceLoader detectors switched on and HTTP plugins registered in config/inconsistency/plugins.yaml. */
public final class DetectorRegistry {

    private static final Pattern DURATION = Pattern.compile("(\\d+)(ms|s|m|h)");

    private final Map<String, InconsistencyDetector> detectors = new LinkedHashMap<>();

    public DetectorRegistry(List<InconsistencyDetector> enabled) {
        enabled.forEach(d -> detectors.put(d.id(), d));
    }

    /** Fails on unknown enabled detectors, a2a plugins and unreadable token files. */
    public static DetectorRegistry load(Path pluginsYaml, Contracts contracts) {
        JsonNode config = contracts.loadConfig("inconsistency-plugins", pluginsYaml);
        Map<String, InconsistencyDetector> available = new LinkedHashMap<>();
        ServiceLoader.load(InconsistencyDetector.class).forEach(d -> available.put(d.id(), d));
        List<InconsistencyDetector> enabled = new java.util.ArrayList<>();
        config.path("detectors").properties().forEach(e -> {
            if (e.getValue().path("enabled").asBoolean(false)) {
                InconsistencyDetector d = available.get(e.getKey());
                if (d == null) {
                    throw new ConfigException("unknown detector '" + e.getKey() + "' in " + pluginsYaml);
                }
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
        return new DetectorRegistry(enabled);
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

    public InconsistencyDetector get(String id) {
        return detectors.get(id);
    }
}
