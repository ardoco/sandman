package edu.kit.kastel.mcse.ardoco.sandman.tlr;

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
import org.testcontainers.containers.GenericContainer;
import org.testcontainers.containers.wait.strategy.Wait;
import tools.jackson.databind.JsonNode;

/** Test wiring: settings for a Memgraph container and a recording event sink. */
final class TestBeans {

    static final Map<String, String> TOKENS =
            Map.of("orchestrator", "orch-token", "tlr-agent", "tlr-token", "query-service", "qs-token");

    static final GenericContainer<?> MEMGRAPH = new GenericContainer<>("memgraph/memgraph:3.13.1")
            .withCommand("--telemetry-enabled=false")
            .withExposedPorts(7687)
            .waitingFor(Wait.forListeningPort());

    static final java.nio.file.Path ARTIFACTS;

    static {
        MEMGRAPH.start();
        try {
            ARTIFACTS = java.nio.file.Files.createTempDirectory("tlr-artifacts-");
        } catch (java.io.IOException e) {
            throw new java.io.UncheckedIOException(e);
        }
    }

    @Produces
    @Singleton
    static Settings settings() {
        Map<String, String> verify = TOKENS.entrySet().stream()
                .collect(Collectors.toMap(Map.Entry::getKey, e -> Ids.sha256Hex(e.getValue())));
        return new Settings(
                "tlr-agent",
                Settings.defaultContractsDir(),
                null,
                null,
                null,
                null,
                "bolt://" + MEMGRAPH.getHost() + ":" + MEMGRAPH.getMappedPort(7687),
                null,
                null,
                null,
                TOKENS.get("tlr-agent"),
                verify,
                null,
                Map.of(),
                ARTIFACTS,
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
            Event event = Event.build(contracts, "tlr-agent", type, subject, data, correlationId, causationId, eventId);
            events.add(event);
            return event;
        }
    }

    private TestBeans() {}
}
