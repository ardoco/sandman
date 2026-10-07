package edu.kit.kastel.mcse.ardoco.sandman.spike;

import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicBoolean;

import edu.kit.kastel.mcse.ardoco.sandman.agent.Contracts;
import io.vertx.ext.web.Router;
import jakarta.enterprise.context.ApplicationScoped;
import jakarta.enterprise.event.Observes;
import jakarta.enterprise.inject.Produces;
import org.a2aproject.sdk.jsonrpc.common.json.JsonUtil;
import org.a2aproject.sdk.server.PublicAgentCard;
import org.a2aproject.sdk.server.agentexecution.AgentExecutor;
import org.a2aproject.sdk.server.agentexecution.RequestContext;
import org.a2aproject.sdk.server.tasks.AgentEmitter;
import org.a2aproject.sdk.spec.AgentCard;
import org.a2aproject.sdk.spec.DataPart;
import org.a2aproject.sdk.spec.Part;
import org.a2aproject.sdk.spec.TextPart;
import tools.jackson.databind.json.JsonMapper;

/** tlr-agent-like spike agent: SANDMAN invocation convention (Stage 2 §1.3) on the a2a-java reference JSON-RPC server. */
@ApplicationScoped
public class Agent implements AgentExecutor {

    static final String EXT = "https://sandman.invalid/a2a/extensions/skill-invocation/v1";
    static final Path CONTRACTS = Path.of(System.getProperty("sandman.contracts", "../../../contracts"));
    static final String TOKEN = System.getenv().getOrDefault("SPIKE_TOKEN", "spike-token");

    final Map<String, AtomicBoolean> running = new ConcurrentHashMap<>();
    final Contracts contracts = new Contracts(CONTRACTS);
    Map<String, Map<String, String>> schemas;

    @Produces
    @PublicAgentCard
    AgentCard card() throws Exception {
        String json = Files.readString(CONTRACTS.resolve("agent-cards/tlr-agent.json"));
        AgentCard card = JsonUtil.fromJson(json, AgentCard.class);
        schemas = new LinkedHashMap<>();
        card.capabilities().extensions().stream().filter(e -> EXT.equals(e.uri()))
                .forEach(e -> schemas.putAll(cast(e.params().get("skills"))));
        return card;
    }

    /** a2a-java serves JSON-RPC at POST /, the card says /a2a: bearer check on both, then reroute /a2a to /. */
    void routes(@Observes Router router) {
        router.postWithRegex("^/(a2a)?$").order(-100).handler(rc -> {
            String auth = rc.request().getHeader("Authorization");
            if (auth == null || !MessageDigest.isEqual(("Bearer " + TOKEN).getBytes(), auth.getBytes())) {
                rc.response().setStatusCode(401).putHeader("Content-Type", "application/json")
                        .end("{\"error\":\"missing or invalid token\"}");
            } else if (rc.request().path().equals("/a2a")) {
                rc.reroute("/");
            } else {
                rc.next();
            }
        });
    }

    @Override
    public void execute(RequestContext ctx, AgentEmitter emitter) {
        if (ctx.getTask() == null) {
            emitter.submit();
        }
        var message = ctx.getMessage();
        Map<String, Object> meta = message.metadata() == null ? Map.of() : message.metadata();
        Object skill = meta.get("sandman.skill");
        if (!(skill instanceof String skillId)) {
            reject(emitter, "INVALID_INPUT", "metadata sandman.skill missing");
            return;
        }
        if (!schemas.containsKey(skillId)) {
            reject(emitter, "UNKNOWN_SKILL", "unknown skill " + skillId);
            return;
        }
        List<DataPart> data = message.parts().stream().filter(DataPart.class::isInstance).map(DataPart.class::cast).toList();
        if (data.size() != 1) {
            reject(emitter, "INVALID_INPUT", "expected exactly one data part");
            return;
        }
        Map<String, Object> input = cast(jsonValue(data.get(0).data()));
        System.out.println("INPUT raw=" + data.get(0).data() + " normalized=" + input);
        try {
            contracts.validate(schemas.get(skillId).get("inputSchema"), JsonMapper.shared().valueToTree(input));
        } catch (Contracts.ContractException e) {
            reject(emitter, "INVALID_INPUT", String.join("; ", e.messages()));
            return;
        }
        var cancelled = new AtomicBoolean();
        running.put(ctx.getTaskId(), cancelled);
        try {
            emitter.startWork();
            Map<String, Object> output = recoverTraceLinks(input, cancelled);
            if (cancelled.get()) {
                return; // cancel() already moved the task to CANCELED
            }
            contracts.validate(schemas.get(skillId).get("outputSchema"), JsonMapper.shared().valueToTree(output));
            emitter.addArtifact(List.of(new DataPart(output)), null, "result", null);
            emitter.complete();
        } catch (Exception e) {
            e.printStackTrace();
            emitter.fail(emitter.newAgentMessage(List.of(new TextPart(e.toString())),
                    Map.of("sandman.error", Map.of("code", "INTERNAL", "message", e.toString(), "retryable", true))));
        } finally {
            running.remove(ctx.getTaskId());
        }
    }

    /** Dummy skill: params.sleepMs makes it cancellable; approaches [ardoco-id] runs ArDoCo on MediaStore (profile ardoco). */
    Map<String, Object> recoverTraceLinks(Map<String, Object> input, AtomicBoolean cancelled) throws Exception {
        Map<String, Object> params = input.get("params") == null ? Map.of() : cast(input.get("params"));
        long sleep = params.get("sleepMs") instanceof Number n ? n.longValue() : 0;
        for (long t = 0; t < sleep && !cancelled.get(); t += 50) {
            Thread.sleep(50);
        }
        List<Object> runs = new ArrayList<>();
        List<String> approaches = input.get("approaches") == null ? List.of("stub-echo") : cast(input.get("approaches"));
        for (String approach : approaches) {
            long count = "ardoco-id".equals(approach)
                    ? ((Number) Class.forName("edu.kit.kastel.mcse.ardoco.sandman.spike.ArdocoProbe").getMethod("run").invoke(null)).longValue()
                    : ((List<?>) input.get("sourceVersionIds")).size() * ((List<?>) input.get("targetVersionIds")).size();
            runs.add(Map.of("approach", approach, "approachVersion", "0", "status", "completed", "linkCount", count,
                    "runId", "run_" + UUID.randomUUID().toString().replace("-", "")));
        }
        return Map.of("runs", runs);
    }

    void reject(AgentEmitter emitter, String code, String message) {
        List<Part<?>> parts = List.of(new TextPart(message));
        emitter.reject(emitter.newAgentMessage(parts,
                Map.of("sandman.error", Map.of("code", code, "message", message, "retryable", false))));
    }

    @Override
    public void cancel(RequestContext ctx, AgentEmitter emitter) {
        var flag = running.get(ctx.getTaskId());
        if (flag != null) {
            flag.set(true);
        }
        emitter.cancel();
    }

    /** Protobuf Struct widens every number to double: integral doubles back to long (same as Python json_value). */
    static Object jsonValue(Object v) {
        if (v instanceof Double d && d == Math.rint(d) && !d.isInfinite()) {
            return d.longValue();
        }
        if (v instanceof Map<?, ?> m) {
            Map<String, Object> out = new LinkedHashMap<>();
            m.forEach((k, x) -> out.put((String) k, jsonValue(x)));
            return out;
        }
        if (v instanceof List<?> l) {
            return l.stream().map(Agent::jsonValue).toList();
        }
        return v;
    }

    @SuppressWarnings("unchecked")
    static <T> T cast(Object o) {
        return (T) o;
    }
}
