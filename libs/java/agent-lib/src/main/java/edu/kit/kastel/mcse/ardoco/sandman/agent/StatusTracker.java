package edu.kit.kastel.mcse.ardoco.sandman.agent;

import java.util.Objects;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

/** Counts active tasks for the system overview (ServiceStatus, contracts/openapi/shared.yaml). */
public final class StatusTracker {

    /** One active task; close() ends it. */
    public interface Active extends AutoCloseable {
        @Override
        void close();
    }

    private final String service;
    private final String version;
    private final AtomicInteger active = new AtomicInteger();
    private volatile String lastActivity;

    public StatusTracker(String service) {
        this(service, Objects.requireNonNullElse(System.getenv("SANDMAN_VERSION"), "dev"));
    }

    public StatusTracker(String service, String version) {
        this.service = service;
        this.version = version;
    }

    /** Marks one task as active until the returned handle is closed (try-with-resources). */
    public Active track() {
        active.incrementAndGet();
        lastActivity = Event.now();
        AtomicBoolean closed = new AtomicBoolean();
        return () -> {
            if (closed.compareAndSet(false, true)) {
                active.decrementAndGet();
                lastActivity = Event.now();
            }
        };
    }

    public int activeTasks() {
        return active.get();
    }

    /** ServiceStatus JSON; details (service-specific) are omitted if null or empty. */
    public ObjectNode snapshot(JsonNode details) {
        int tasks = active.get();
        ObjectNode status = JsonMapper.shared()
                .createObjectNode()
                .put("service", service)
                .put("version", version)
                .put("state", tasks > 0 ? "working" : "idle")
                .put("activeTasks", tasks);
        if (lastActivity != null) {
            status.put("lastActivityAt", lastActivity);
        }
        if (details != null && !details.isEmpty()) {
            status.set("details", details);
        }
        return status;
    }
}
