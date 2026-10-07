package edu.kit.kastel.mcse.ardoco.sandman.agent.a2a;

import edu.kit.kastel.mcse.ardoco.sandman.agent.Event;
import tools.jackson.databind.JsonNode;

/** Completion event publishing as CDI bean; production: {@code EventPublisher::publish}. Absent for plugins. */
@FunctionalInterface
public interface EventSink {

    /** Publishes and returns after the ack; throws if the event could not be published. */
    Event publish(String type, String subject, JsonNode data, String correlationId, String causationId, String eventId);
}
