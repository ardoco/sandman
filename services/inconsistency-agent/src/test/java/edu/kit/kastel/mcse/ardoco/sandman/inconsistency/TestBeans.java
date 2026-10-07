package edu.kit.kastel.mcse.ardoco.sandman.inconsistency;

import edu.kit.kastel.mcse.ardoco.sandman.agent.Contracts;
import edu.kit.kastel.mcse.ardoco.sandman.agent.Event;
import edu.kit.kastel.mcse.ardoco.sandman.agent.Ids;
import edu.kit.kastel.mcse.ardoco.sandman.agent.Settings;
import edu.kit.kastel.mcse.ardoco.sandman.agent.a2a.EventSink;
import jakarta.enterprise.inject.Produces;
import jakarta.inject.Inject;
import jakarta.inject.Singleton;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.stream.Collectors;
import tools.jackson.databind.JsonNode;

/** Test wiring: settings for the Memgraph container and a recording event sink. */
final class TestBeans {

    static final Map<String, String> TOKENS =
            Map.of("orchestrator", "orch-token", "query-service", "qs-token", "plugin:p", "plugin-token");

    private TestBeans() {}

    @Produces
    @Singleton
    static Settings settings() {
        Map<String, String> verify = TOKENS.entrySet().stream()
                .collect(Collectors.toMap(Map.Entry::getKey, e -> Ids.sha256Hex(e.getValue())));
        return new Settings(
                "inconsistency-agent",
                Settings.defaultContractsDir(),
                null,
                null,
                null,
                null,
                TestKg.uri(),
                null,
                null,
                null,
                "own-token",
                verify,
                null,
                null,
                null,
                null);
    }

    /** Records published events (validated envelopes). */
    @Singleton // no client proxy: tests read the list directly
    static class Events implements EventSink {
        final List<Event> events = new CopyOnWriteArrayList<>();

        @Inject
        Contracts contracts;

        @Override
        public Event publish(
                String type, String subject, JsonNode data, String correlationId, String causationId, String eventId) {
            Event event = Event.build(
                    contracts, "inconsistency-agent", type, subject, data, correlationId, causationId, eventId);
            events.add(event);
            return event;
        }
    }
}
