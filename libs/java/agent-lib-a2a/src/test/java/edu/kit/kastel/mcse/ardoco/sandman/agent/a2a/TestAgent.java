package edu.kit.kastel.mcse.ardoco.sandman.agent.a2a;

import edu.kit.kastel.mcse.ardoco.sandman.agent.Contracts;
import edu.kit.kastel.mcse.ardoco.sandman.agent.Event;
import edu.kit.kastel.mcse.ardoco.sandman.agent.Ids;
import edu.kit.kastel.mcse.ardoco.sandman.agent.Settings;
import jakarta.enterprise.inject.Produces;
import jakarta.inject.Inject;
import jakarta.inject.Singleton;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.function.Function;
import java.util.stream.Collectors;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

/** Test agent beans: tlr-agent settings, a recording event sink and a configurable recover-trace-links skill. */
final class TestAgent {

    static final Map<String, String> TOKENS =
            Map.of("orchestrator", "orch-token", "tlr-agent", "tlr-token", "plugin:plugin-stubs", "plugin-token");
    static final JsonNode OUTPUT = JsonMapper.shared()
            .readTree("{\"runs\":[{\"approach\":\"stub\",\"approachVersion\":\"0\",\"runId\":\"run_" + "0".repeat(32)
                    + "\",\"status\":\"completed\",\"linkCount\":1}]}");

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
                null,
                null,
                null,
                null,
                TOKENS.get("tlr-agent"),
                verify,
                null,
                Map.of(),
                null,
                null,
                null);
    }

    /** Records published events (validated envelopes) and the task state seen at publish time. */
    @Singleton // no client proxy: tests read the fields directly
    static class Events implements EventSink {
        final List<Event> events = new CopyOnWriteArrayList<>();
        final List<String> statesAtPublish = new CopyOnWriteArrayList<>();
        volatile Function<String, String> stateProbe = taskId -> null;
        volatile boolean fail;

        @Inject
        Contracts contracts;

        @Inject
        Recover skill;

        @Override
        public Event publish(
                String type, String subject, JsonNode data, String correlationId, String causationId, String eventId) {
            if (fail) {
                throw new IllegalStateException("NATS down");
            }
            statesAtPublish.add(stateProbe.apply(skill.seen.getLast().taskId()));
            Event event = Event.build(contracts, "tlr-agent", type, subject, data, correlationId, causationId, eventId);
            events.add(event);
            return event;
        }
    }

    /** recover-trace-links with configurable output, failure and blocking. */
    @Singleton // no client proxy: tests read the fields directly
    static class Recover implements Skill {
        final List<SkillContext> seen = new CopyOnWriteArrayList<>();
        volatile JsonNode output = OUTPUT;
        volatile Exception error;
        volatile Error fatal;
        volatile String fatalFor;
        volatile CountDownLatch started;
        volatile CountDownLatch release;

        @Override
        public String id() {
            return "recover-trace-links";
        }

        @Override
        public SkillResult run(SkillContext ctx, JsonNode input) throws SkillException {
            seen.add(ctx);
            boolean failing = fatal != null && (fatalFor == null || fatalFor.equals(ctx.messageId()));
            if (release != null && !failing) {
                started.countDown();
                try {
                    while (!release.await(20, TimeUnit.MILLISECONDS) && !ctx.cancelled()) {
                        // wait for the test or a cancellation
                    }
                } catch (InterruptedException e) {
                    Thread.currentThread().interrupt();
                }
            }
            if (failing) {
                throw fatal;
            }
            if (error instanceof SkillException e) {
                throw e;
            }
            if (error instanceof RuntimeException e) {
                throw e;
            }
            ObjectNode event = JsonMapper.shared().createObjectNode();
            event.set("projectId", input.get("projectId"));
            event.set("sourceVersionIds", input.get("sourceVersionIds"));
            event.set("targetVersionIds", input.get("targetVersionIds"));
            event.set("runs", output.path("runs"));
            return new SkillResult(output, "sandman.tracelinks.updated", event);
        }
    }
}
