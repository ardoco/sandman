package edu.kit.kastel.mcse.ardoco.sandman.inconsistency;

import edu.kit.kastel.mcse.ardoco.sandman.agent.Contracts;
import edu.kit.kastel.mcse.ardoco.sandman.agent.EventPublisher;
import edu.kit.kastel.mcse.ardoco.sandman.agent.KgClient;
import edu.kit.kastel.mcse.ardoco.sandman.agent.RunRecorder;
import edu.kit.kastel.mcse.ardoco.sandman.agent.Settings;
import edu.kit.kastel.mcse.ardoco.sandman.agent.Streams;
import edu.kit.kastel.mcse.ardoco.sandman.agent.a2a.EventSink;
import io.nats.client.Connection;
import io.nats.client.JetStreamApiException;
import io.nats.client.Nats;
import io.quarkus.arc.DefaultBean;
import io.quarkus.runtime.StartupEvent;
import jakarta.enterprise.context.ApplicationScoped;
import jakarta.enterprise.event.Observes;
import jakarta.enterprise.inject.Disposes;
import jakarta.enterprise.inject.Produces;
import jakarta.inject.Singleton;
import java.io.IOException;
import java.nio.file.Path;
import java.util.Objects;
import java.util.Optional;
import org.eclipse.microprofile.config.inject.ConfigProperty;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/** Production wiring: KG, detectors, services and the JetStream event sink. */
@ApplicationScoped
public class Beans {

    private static final Logger LOG = LoggerFactory.getLogger(Beans.class);

    private EventPublisher publisher;

    @ConfigProperty(name = "sandman.inconsistency.plugins-file")
    Optional<Path> pluginsFile;

    @ConfigProperty(name = "sandman.query-service-url", defaultValue = "http://query-service:8000")
    String queryServiceUrl;

    @Produces
    @Singleton
    KgClient kg(Settings settings) {
        settings.require("kgUri");
        return new KgClient(settings.kgUri(), settings.kgUser(), settings.kgPassword());
    }

    void close(@Disposes KgClient kg) {
        kg.close();
    }

    @Produces
    @Singleton
    DetectorRegistry registry(Settings settings, Contracts contracts) {
        Path file = pluginsFile.orElseGet(() -> (settings.configDir() != null
                        ? settings.configDir()
                        : settings.contractsDir().resolveSibling("config"))
                .resolve("inconsistency/plugins.yaml"));
        return DetectorRegistry.load(file, contracts);
    }

    @Produces
    @Singleton
    DetectionService detection(KgClient kg, Contracts contracts, DetectorRegistry registry, Settings settings) {
        RunRecorder runs = new RunRecorder(
                kg, settings.serviceName(), Objects.requireNonNullElse(System.getenv("SANDMAN_VERSION"), "dev"));
        return new DetectionService(
                kg, contracts, runs, registry, queryServiceUrl, new Notifications(settings.logFile()));
    }

    @Produces
    @Singleton
    TriageService triage(KgClient kg, EventSink events) {
        return new TriageService(kg, events);
    }

    /** JetStream-backed sink; the connection opens on the first event. */
    @Produces
    @DefaultBean
    @Singleton
    EventSink eventSink(Settings settings, Contracts contracts) {
        return (type, subject, data, correlationId, causationId, eventId) ->
                publisher(settings, contracts).publish(type, subject, data, correlationId, causationId, eventId);
    }

    private synchronized EventPublisher publisher(Settings settings, Contracts contracts) {
        if (publisher == null) {
            try {
                settings.require("natsUrl");
                Connection nats = Nats.connect(settings.natsUrl());
                Streams.ensure(nats.jetStreamManagement());
                publisher = new EventPublisher(nats.jetStream(), contracts, settings.serviceName());
            } catch (IOException | JetStreamApiException e) {
                throw new IllegalStateException("cannot connect to NATS", e);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                throw new IllegalStateException(e);
            }
        }
        return publisher;
    }

    void onStart(@Observes StartupEvent event, KgClient kg, Settings settings, DetectorRegistry registry) {
        kg.initSchema(settings.contractsDir().resolve("kg/init.cypher"));
        LOG.info(
                "inconsistency-agent ready, detectors {}",
                registry.all().stream().map(InconsistencyDetector::id).toList());
    }
}
