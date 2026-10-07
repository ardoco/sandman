package edu.kit.kastel.mcse.ardoco.sandman.tlr;

import edu.kit.kastel.mcse.ardoco.sandman.agent.Contracts;
import edu.kit.kastel.mcse.ardoco.sandman.agent.EventPublisher;
import edu.kit.kastel.mcse.ardoco.sandman.agent.KgClient;
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
import java.util.Optional;
import java.util.ServiceLoader;
import org.eclipse.microprofile.config.inject.ConfigProperty;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/** Production wiring: KG, approaches, NATS event sink and the startup deactivation of disabled approaches. */
@ApplicationScoped
public class Beans {

    private static final Logger LOG = LoggerFactory.getLogger(Beans.class);

    private EventPublisher publisher;

    @ConfigProperty(name = "sandman.tlr.approaches-file")
    Optional<Path> approachesFile;

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
    Approaches approaches(Settings settings) {
        Path file = approachesFile.orElseGet(() -> (settings.configDir() != null
                        ? settings.configDir()
                        : settings.contractsDir().resolveSibling("config"))
                .resolve("tlr/approaches.yaml"));
        return new Approaches(file, ServiceLoader.load(TraceLinkApproach.class));
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

    void onStart(@Observes StartupEvent event, KgClient kg, Settings settings, Approaches approaches) {
        kg.initSchema(settings.contractsDir().resolve("kg/init.cypher"));
        new LinkStore(kg).deactivate(approaches.disabledIds());
        LOG.info("tlr-agent ready, approaches {}, deactivated links of {}", approaches, approaches.disabledIds());
    }
}
