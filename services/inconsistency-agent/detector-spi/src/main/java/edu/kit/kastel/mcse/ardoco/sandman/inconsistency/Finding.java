package edu.kit.kastel.mcse.ardoco.sandman.inconsistency;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ArrayNode;
import tools.jackson.databind.node.ObjectNode;

/** Detector output before fingerprinting (skills.schema.json Finding). */
public record Finding(
        String type,
        String severity,
        String title,
        String description,
        Double confidence,
        List<Involved> involved,
        Map<String, Object> evidence) {

    /** Involved element (by key) or trace link (by id). */
    public record Involved(String elementKey, String traceLinkId, String role) {
        public static Involved element(String key, String role) {
            return new Involved(key, null, role);
        }
    }

    // Manual JSON mapping: Jackson 3 bean introspection needs jackson-annotations 2.22, the Quarkus BOM pins 2.21.
    public ObjectNode toJson() {
        ObjectNode n = JsonMapper.shared()
                .createObjectNode()
                .put("type", type)
                .put("severity", severity)
                .put("title", title);
        if (description != null) {
            n.put("description", description);
        }
        if (confidence != null) {
            n.put("confidence", confidence);
        }
        ArrayNode array = n.putArray("involved");
        for (Involved i : involved) {
            ObjectNode o = array.addObject();
            if (i.elementKey() != null) {
                o.put("elementKey", i.elementKey());
            }
            if (i.traceLinkId() != null) {
                o.put("traceLinkId", i.traceLinkId());
            }
            o.put("role", i.role());
        }
        if (evidence != null) {
            n.set("evidence", JsonMapper.shared().valueToTree(evidence));
        }
        return n;
    }

    /** Input must have passed the Finding schema. */
    public static Finding fromJson(JsonNode n) {
        List<Involved> involved = new ArrayList<>();
        n.get("involved")
                .forEach(i ->
                        involved.add(new Involved(text(i, "elementKey"), text(i, "traceLinkId"), text(i, "role"))));
        return new Finding(
                text(n, "type"),
                text(n, "severity"),
                text(n, "title"),
                text(n, "description"),
                n.hasNonNull("confidence") ? n.get("confidence").asDouble() : null,
                involved,
                n.hasNonNull("evidence") ? JsonMapper.shared().convertValue(n.get("evidence"), Map.class) : null);
    }

    private static String text(JsonNode n, String field) {
        return n.hasNonNull(field) ? n.get(field).asString() : null;
    }
}
