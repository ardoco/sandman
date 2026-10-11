package edu.kit.kastel.mcse.ardoco.sandman.agent.a2a;

import edu.kit.kastel.mcse.ardoco.sandman.agent.Contracts;
import edu.kit.kastel.mcse.ardoco.sandman.agent.ServiceAuth;
import edu.kit.kastel.mcse.ardoco.sandman.agent.Settings;
import edu.kit.kastel.mcse.ardoco.sandman.agent.StatusTracker;
import edu.kit.kastel.mcse.ardoco.sandman.agent.Telemetry;
import jakarta.annotation.PostConstruct;
import jakarta.enterprise.context.ApplicationScoped;
import jakarta.enterprise.inject.Instance;
import jakarta.inject.Inject;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.function.Function;
import java.util.function.IntConsumer;
import java.util.stream.Collectors;
import org.a2aproject.sdk.server.agentexecution.AgentExecutor;
import org.a2aproject.sdk.server.agentexecution.RequestContext;
import org.a2aproject.sdk.server.tasks.AgentEmitter;
import org.a2aproject.sdk.spec.DataPart;
import org.a2aproject.sdk.spec.Message;
import org.a2aproject.sdk.spec.TextPart;
import org.a2aproject.sdk.transport.jsonrpc.context.JSONRPCContextKeys;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.SerializationFeature;
import tools.jackson.databind.json.JsonMapper;

/**
 * SANDMAN skill invocation convention (Stage 2 §1.3): skill id in message.metadata['sandman.skill'], input = one data
 * part, result = artifact 'result' with one data part, errors in status message metadata 'sandman.error'. Agent skills
 * publish exactly one completion event (causationid = request messageId) after the output was validated and before the
 * task completes; plugin skills publish none.
 */
@ApplicationScoped
public class SkillExecutor implements AgentExecutor {

    public static final String EXTENSION = "https://sandman.invalid/a2a/extensions/skill-invocation/v1";
    public static final String SKILL_KEY = "sandman.skill";
    public static final String WORKFLOW_KEY = "sandman.workflowId";
    public static final String ACTOR_KEY = "sandman.actor";
    public static final String ERROR_KEY = "sandman.error";

    private static final Logger LOG = LoggerFactory.getLogger(SkillExecutor.class);

    @Inject
    A2aServer server;

    @Inject
    Settings settings;

    @Inject
    Contracts contracts;

    @Inject
    StatusTracker status;

    @Inject
    Instance<Skill> skillBeans;

    @Inject
    Instance<EventSink> events;

    static final int VM_ERROR_EXIT_CODE = 70;
    private static final AtomicBoolean DRAINING = new AtomicBoolean();

    /** Ends the JVM once drained; compose restarts the container. Replaced in tests. */
    static volatile IntConsumer terminator = code -> {
        System.out.flush();
        System.err.flush();
        Runtime.getRuntime().halt(code);
    };

    /** How long in-flight tasks may finish while draining (SANDMAN_DRAIN_TIMEOUT_SECONDS, default 120). */
    static volatile long drainTimeoutMillis = drainTimeoutFromEnv();

    private static long drainTimeoutFromEnv() {
        String value = System.getenv("SANDMAN_DRAIN_TIMEOUT_SECONDS");
        try {
            return value == null || value.isBlank() ? 120_000 : Long.parseLong(value.strip()) * 1000;
        } catch (NumberFormatException e) {
            throw new IllegalStateException("SANDMAN_DRAIN_TIMEOUT_SECONDS is not a number: " + value, e);
        }
    }

    /** True from the first damaging error on: new tasks are refused and the health endpoints report down. */
    static boolean draining() {
        return DRAINING.get();
    }

    /** For tests. */
    static void resetDraining() {
        DRAINING.set(false);
    }

    private void drainAndExit() {
        if (!DRAINING.compareAndSet(false, true)) {
            return;
        }
        LOG.error("draining for at most {} ms, then exiting for a restart", drainTimeoutMillis);
        Thread drain = new Thread(
                () -> {
                    long deadline = System.nanoTime() + drainTimeoutMillis * 1_000_000;
                    try {
                        while (status.activeTasks() > 0 && System.nanoTime() < deadline) {
                            Thread.sleep(100);
                        }
                    } catch (InterruptedException e) {
                        // exit now
                    }
                    terminator.accept(VM_ERROR_EXIT_CODE);
                },
                "drain-and-exit");
        drain.setDaemon(true);
        drain.start();
    }

    private static final int MAX_SEEN = 1024;
    private static final JsonMapper CANONICAL = JsonMapper.builder()
            .enable(SerializationFeature.ORDER_MAP_ENTRIES_BY_KEYS)
            .build();

    private record Outcome(Object output, SkillException error, boolean reject) {}

    private final Map<String, SkillContext> running = new ConcurrentHashMap<>();

    private record Seen(String fingerprint, CompletableFuture<Outcome> outcome) {}

    private final Map<List<String>, Seen> seen = Collections.synchronizedMap(new LinkedHashMap<>() {
        @Override
        protected boolean removeEldestEntry(Map.Entry<List<String>, Seen> eldest) {
            if (size() > MAX_SEEN) {
                entrySet().stream()
                        .filter(e -> e.getValue().outcome().isDone())
                        .findFirst()
                        .ifPresent(e -> remove(e.getKey()));
            }
            return false;
        }
    });
    private Map<String, Skill> skills;

    @PostConstruct
    void init() {
        skills = skillBeans.stream().collect(Collectors.toMap(Skill::id, Function.identity()));
        List<String> missing = skills.keySet().stream()
                .filter(id -> !server.schemas().containsKey(id))
                .toList();
        if (!missing.isEmpty()) {
            throw new IllegalStateException("skills without schemas in the agent card: " + missing);
        }
    }

    @Override
    public void execute(RequestContext context, AgentEmitter emitter) {
        if (context.getTask() == null) {
            emitter.submit();
        }
        if (DRAINING.get()) {
            end(
                    emitter,
                    new SkillException("DEPENDENCY_UNAVAILABLE", "service is restarting, retry later", true),
                    false);
            return;
        }
        Message message = context.getMessage();
        Map<String, Object> metadata = message.metadata() == null ? Map.of() : message.metadata();
        String caller = Objects.requireNonNullElse(server.authenticate(authorization(context)), "");
        if (!(metadata.get(SKILL_KEY) instanceof String skillId)) {
            end(emitter, new SkillException("INVALID_INPUT", "metadata " + SKILL_KEY + " missing", false), true);
            return;
        }
        Skill skill = skills.get(skillId);
        if (skill == null && server.plannedSkills().contains(skillId)) {
            end(
                    emitter,
                    new SkillException("NOT_IMPLEMENTED", "skill " + skillId + " is planned, not implemented", false),
                    true);
            return;
        }
        if (skill == null) {
            end(emitter, new SkillException("UNKNOWN_SKILL", "unknown skill " + skillId, false), true);
            return;
        }
        List<DataPart> data = message.parts().stream()
                .filter(DataPart.class::isInstance)
                .map(DataPart.class::cast)
                .toList();
        if (data.size() != 1) {
            end(emitter, new SkillException("INVALID_INPUT", "expected exactly one data part", false), true);
            return;
        }
        JsonNode input = toJson(data.get(0).data());
        try {
            contracts.validate(server.schemas().get(skillId).get("inputSchema"), input);
        } catch (Contracts.ContractException e) {
            end(emitter, new SkillException("INVALID_INPUT", e.getMessage(), false), true);
            return;
        }
        String actor;
        try {
            actor = ServiceAuth.actorFromMetadata(caller, metadata);
        } catch (ServiceAuth.ForbiddenException e) {
            end(emitter, new SkillException("FORBIDDEN", e.getMessage(), false), true);
            return;
        }
        CompletableFuture<Outcome> done = new CompletableFuture<>();
        List<String> key = List.of(caller, message.messageId());
        String fingerprint = skillId + "\n"
                + CANONICAL.writeValueAsString(JsonMapper.shared().convertValue(input, Object.class));
        Seen first = seen.putIfAbsent(key, new Seen(fingerprint, done));
        if (first != null) {
            if (!first.fingerprint().equals(fingerprint)) {
                end(
                        emitter,
                        new SkillException("INVALID_INPUT", "messageId reused with a different request", false),
                        true);
                return;
            }
            replay(first.outcome(), emitter);
            return;
        }
        SkillContext ctx = new SkillContext(
                context.getTaskId(),
                message.messageId(),
                metadata.get(WORKFLOW_KEY) instanceof String w ? w : null,
                caller,
                actor,
                emitter);
        running.put(ctx.taskId(), ctx);
        try (var active = status.track()) {
            Telemetry.a2aTask(settings.serviceName(), skillId, ctx.taskId(), headers(context), () -> {
                emitter.startWork();
                return run(skill, ctx, input, emitter, done);
            });
        } finally {
            running.remove(ctx.taskId());
            if (!done.isDone()) {
                seen.values().removeIf(s -> s.outcome() == done);
                done.complete(new Outcome(
                        null, new SkillException("INTERNAL", "first attempt was interrupted", true), false));
            }
        }
    }

    private static void replay(CompletableFuture<Outcome> first, AgentEmitter emitter) {
        emitter.startWork();
        Outcome o = first.join();
        if (o.error() != null) {
            end(emitter, o.error(), o.reject());
            return;
        }
        emitter.addArtifact(List.of(new DataPart(o.output())), null, "result", null);
        emitter.complete();
    }

    private String run(
            Skill skill, SkillContext ctx, JsonNode input, AgentEmitter emitter, CompletableFuture<Outcome> done) {
        SkillResult result;
        try {
            result = skill.run(ctx, input);
        } catch (SkillException e) {
            return end(emitter, done, e, false);
        } catch (StackOverflowError e) { // input driven; the stack unwound, the JVM is usable
            LOG.error("skill {} overflowed the stack", skill.id());
            return end(
                    emitter,
                    done,
                    new SkillException("INVALID_INPUT", "input is too deeply nested to process", false),
                    false);
        } catch (VirtualMachineError e) { // OutOfMemoryError, InternalError, UnknownError: state may be damaged
            try {
                LOG.error("skill {} hit a virtual machine error", skill.id(), e);
                return end(
                        emitter,
                        done,
                        new SkillException("INTERNAL", e.getClass().getSimpleName() + ": " + e.getMessage(), false),
                        false);
            } finally {
                drainAndExit();
            }
        } catch (ExceptionInInitializerError | NoClassDefFoundError e) { // the class stays unusable until a restart
            try {
                LOG.error("skill {} hit a failed class initialization", skill.id(), e);
                return end(
                        emitter,
                        done,
                        new SkillException("INTERNAL", e.getClass().getSimpleName() + ": " + e.getMessage(), true),
                        false);
            } finally {
                drainAndExit();
            }
        } catch (RuntimeException
                | Error e) { // unexpected failures (also linkage errors) are reported as retryable INTERNAL
            LOG.error("skill {} failed", skill.id(), e);
            return end(
                    emitter,
                    done,
                    new SkillException("INTERNAL", e.getClass().getSimpleName() + ": " + e.getMessage(), true),
                    false);
        }
        if (ctx.cancelled()) {
            return "canceled"; // cancel() already moved the task to CANCELED
        }
        try {
            contracts.validate(server.schemas().get(skill.id()).get("outputSchema"), result.output());
        } catch (Contracts.ContractException e) {
            return end(
                    emitter, done, new SkillException("INTERNAL", "invalid output: " + e.getMessage(), false), false);
        }
        if (result.eventType() != null) {
            if (!events.isResolvable() || result.eventData() == null) {
                return end(
                        emitter,
                        done,
                        new SkillException("INTERNAL", "completion event without event sink", false),
                        false);
            }
            try {
                events.get()
                        .publish(
                                result.eventType(),
                                input.path("projectId").asString(""),
                                result.eventData(),
                                ctx.workflowId(),
                                ctx.messageId(),
                                null);
            } catch (RuntimeException e) { // KG is committed; the retried task republishes
                LOG.error("publishing completion event failed", e);
                return end(emitter, done, new SkillException("DEPENDENCY_UNAVAILABLE", e.toString(), true), false);
            }
        }
        Object output = JsonMapper.shared().convertValue(result.output(), Object.class);
        emitter.addArtifact(List.of(new DataPart(output)), null, "result", null);
        emitter.complete();
        done.complete(new Outcome(output, null, false));
        return "completed";
    }

    private String end(AgentEmitter emitter, CompletableFuture<Outcome> done, SkillException error, boolean reject) {
        if (error.retryable()) {
            seen.values().removeIf(s -> s.outcome() == done);
        }
        done.complete(new Outcome(null, error, reject));
        return end(emitter, error, reject);
    }

    private static String end(AgentEmitter emitter, SkillException error, boolean reject) {
        Map<String, Object> json =
                Map.of("code", error.code(), "message", error.getMessage(), "retryable", error.retryable());
        Message msg = emitter.newAgentMessage(List.of(new TextPart(error.getMessage())), Map.of(ERROR_KEY, json));
        if (reject) {
            emitter.reject(msg);
        } else {
            emitter.fail(msg);
        }
        return reject ? "rejected" : "failed";
    }

    @Override
    public void cancel(RequestContext context, AgentEmitter emitter) {
        SkillContext ctx = running.get(context.getTaskId());
        if (ctx != null) {
            ctx.cancel();
        }
        emitter.cancel();
    }

    private static Map<?, ?> headers(RequestContext context) {
        if (context.getCallContext() == null
                || !(context.getCallContext().getState().get(JSONRPCContextKeys.HEADERS_KEY)
                        instanceof Map<?, ?> headers)) {
            return Map.of();
        }
        return headers;
    }

    private static String authorization(RequestContext context) {
        for (Map.Entry<?, ?> h : headers(context).entrySet()) {
            if ("authorization".equalsIgnoreCase(String.valueOf(h.getKey()))) {
                return String.valueOf(h.getValue());
            }
        }
        return null;
    }

    /** Data part value as JSON, integral doubles back to integers (protobuf Struct widens every number, W-4). */
    static JsonNode toJson(Object value) {
        return JsonMapper.shared().valueToTree(jsonValue(value));
    }

    static Object jsonValue(Object v) {
        if (v instanceof Double d && d == Math.rint(d) && Math.abs(d) <= 0x1p53) { // exact integers only
            long l = d.longValue();
            return l == (int) l ? (Object) (int) l : l; // same node types as parsed JSON
        }
        if (v instanceof Map<?, ?> m) {
            Map<String, Object> out = new LinkedHashMap<>();
            m.forEach((k, x) -> out.put((String) k, jsonValue(x)));
            return out;
        }
        if (v instanceof List<?> l) {
            return l.stream().map(SkillExecutor::jsonValue).toList();
        }
        return v;
    }

    @SuppressWarnings("unchecked")
    static <T> T cast(Object o) {
        return (T) o;
    }
}
