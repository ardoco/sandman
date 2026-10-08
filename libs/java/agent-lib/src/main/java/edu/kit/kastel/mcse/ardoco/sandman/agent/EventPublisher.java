package edu.kit.kastel.mcse.ardoco.sandman.agent;

import io.nats.client.JetStream;
import io.nats.client.impl.Headers;
import java.time.Duration;
import java.util.List;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

/** Publishes to JetStream: Nats-Msg-Id = event id, waits for the ack, retries 3x with backoff, then throws. */
public final class EventPublisher {

    private static final Logger LOG = LoggerFactory.getLogger(EventPublisher.class);
    private static final List<Duration> DEFAULT_BACKOFF =
            List.of(Duration.ofMillis(500), Duration.ofSeconds(2), Duration.ofSeconds(5));

    /** Event could not be published after retries. */
    public static class PublishException extends RuntimeException {
        public PublishException(String message, Throwable cause) {
            super(message, cause);
        }
    }

    private final JetStream js;
    private final Contracts contracts;
    private final String serviceName;
    private final List<Duration> backoff;

    public EventPublisher(JetStream js, Contracts contracts, String serviceName) {
        this(js, contracts, serviceName, DEFAULT_BACKOFF);
    }

    public EventPublisher(JetStream js, Contracts contracts, String serviceName, List<Duration> backoff) {
        this.js = js;
        this.contracts = contracts;
        this.serviceName = serviceName;
        this.backoff = List.copyOf(backoff);
    }

    /** Validates, publishes and waits for the JetStream ack; nullable ids are omitted or generated. */
    public Event publish(
            String type, String subject, JsonNode data, String correlationId, String causationId, String eventId) {
        return Telemetry.inSpan(
                Telemetry.publishSpan(type),
                () -> publishInSpan(type, subject, data, correlationId, causationId, eventId));
    }

    private Event publishInSpan(
            String type, String subject, JsonNode data, String correlationId, String causationId, String eventId) {
        Event event = Event.build(contracts, serviceName, type, subject, data, correlationId, causationId, eventId);
        byte[] payload = JsonMapper.shared().writeValueAsBytes(event.toJson());
        Headers headers = new Headers().add("Nats-Msg-Id", event.id());
        for (int attempt = 0; ; attempt++) {
            try {
                js.publish(type, headers, payload);
                Telemetry.eventPublished(type);
                return event;
            } catch (Exception e) { // any transport error is retried, then surfaced
                if (attempt == backoff.size()) {
                    throw new PublishException(
                            "publishing %s %s failed after %d attempts".formatted(type, event.id(), attempt + 1), e);
                }
                LOG.warn("publish failed, retrying (event {}, attempt {}): {}", type, attempt + 1, e.toString());
                try {
                    Thread.sleep(backoff.get(attempt));
                } catch (InterruptedException ie) {
                    Thread.currentThread().interrupt();
                    throw new PublishException("interrupted while publishing " + type + " " + event.id(), ie);
                }
            }
        }
    }
}
