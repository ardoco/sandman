package edu.kit.kastel.mcse.ardoco.sandman.tlr;

import edu.kit.kastel.mcse.ardoco.sandman.agent.Contracts;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.stream.Collectors;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

/** Approaches found via ServiceLoader and switched on in config/tlr/approaches.yaml. */
public final class Approaches {

    /** An enabled approach with its configured default parameters. */
    public record Entry(TraceLinkApproach approach, ObjectNode params) {}

    private final Map<String, Entry> enabled = new LinkedHashMap<>();
    private final Set<String> disabled;

    public Approaches(Path file, Iterable<TraceLinkApproach> available, Contracts contracts) {
        JsonNode config = contracts.loadConfig("tlr-approaches", file).path("approaches");
        for (TraceLinkApproach a : available) {
            JsonNode c = config.path(a.id());
            if (c.path("enabled").asBoolean(false)) {
                enabled.put(
                        a.id(),
                        new Entry(
                                a,
                                c.path("params") instanceof ObjectNode p
                                        ? p
                                        : JsonMapper.shared().createObjectNode()));
            }
        }
        List<String> off = new ArrayList<>();
        config.properties().forEach(e -> {
            if (!e.getValue().path("enabled").asBoolean(false)) {
                off.add(e.getKey());
            }
        });
        disabled = Set.copyOf(off);
    }

    public List<Entry> enabled() {
        return List.copyOf(enabled.values());
    }

    /** Enabled approach or null. */
    public Entry get(String id) {
        return enabled.get(id);
    }

    /** Ids configured with enabled=false (approaches absent from the file, e.g. explicit-reference, are untouched). */
    public Set<String> disabledIds() {
        return disabled;
    }

    /** Effective parameters: configured defaults overridden member-wise (shallow) by the request. */
    public static ObjectNode effective(ObjectNode defaults, JsonNode override) {
        ObjectNode params = defaults.deepCopy();
        override.properties().forEach(e -> params.set(e.getKey(), e.getValue()));
        return params;
    }

    @Override
    public String toString() {
        return enabled.keySet().stream().collect(Collectors.joining(", ", "Approaches[", "]"));
    }
}
