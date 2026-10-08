package edu.kit.kastel.mcse.ardoco.sandman.agent.a2a;

import edu.kit.kastel.mcse.ardoco.sandman.agent.Telemetry;
import io.opentelemetry.api.OpenTelemetry;
import io.quarkus.runtime.StartupEvent;
import jakarta.enterprise.context.ApplicationScoped;
import jakarta.enterprise.event.Observes;
import jakarta.enterprise.inject.Instance;

/** Hands the quarkus-opentelemetry instance (if the service has the extension) to the library. */
@ApplicationScoped
class TelemetryBridge {

    void onStart(@Observes StartupEvent event, Instance<OpenTelemetry> otel) {
        if (otel.isResolvable()) {
            Telemetry.use(otel.get());
        }
    }
}
