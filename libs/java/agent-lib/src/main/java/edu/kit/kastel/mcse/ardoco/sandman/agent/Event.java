package edu.kit.kastel.mcse.ardoco.sandman.agent;

import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.UUID;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

/** CloudEvents 1.0 envelope (ADR-0003); subject = projectId. */
public record Event(
        String id,
        String type,
        String source,
        String subject,
        String time,
        JsonNode data,
        String correlationId,
        String causationId,
        String traceparent) {

    /** Creates an event from service name and payload and validates it against events.schema.json. */
    public static Event build(
            Contracts contracts,
            String serviceName,
            String type,
            String subject,
            JsonNode data,
            String correlationId,
            String causationId,
            String eventId) {
        Event event = new Event(
                eventId != null ? eventId : UUID.randomUUID().toString(),
                type,
                "sandman/" + serviceName,
                subject,
                now(),
                data,
                correlationId,
                causationId,
                null);
        contracts.validate("events.schema.json", event.toJson());
        return event;
    }

    /** ISO-8601 UTC timestamp with seconds precision. */
    static String now() {
        return Instant.now().truncatedTo(ChronoUnit.SECONDS).toString();
    }

    /** Envelope as JSON object without null members. */
    public ObjectNode toJson() {
        ObjectNode envelope = JsonMapper.shared().createObjectNode();
        envelope.put("specversion", "1.0")
                .put("id", id)
                .put("source", source)
                .put("type", type)
                .put("subject", subject)
                .put("time", time)
                .put("datacontenttype", "application/json")
                .set("data", data);
        if (correlationId != null) {
            envelope.put("correlationid", correlationId);
        }
        if (causationId != null) {
            envelope.put("causationid", causationId);
        }
        if (traceparent != null) {
            envelope.put("traceparent", traceparent);
        }
        return envelope;
    }

    public static Event fromJson(JsonNode envelope) {
        return new Event(
                envelope.get("id").asString(),
                envelope.get("type").asString(),
                envelope.get("source").asString(),
                envelope.get("subject").asString(),
                envelope.get("time").asString(),
                envelope.get("data"),
                optional(envelope, "correlationid"),
                optional(envelope, "causationid"),
                optional(envelope, "traceparent"));
    }

    private static String optional(JsonNode envelope, String name) {
        return envelope.hasNonNull(name) ? envelope.get(name).asString() : null;
    }
}
