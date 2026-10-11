package edu.kit.kastel.mcse.ardoco.sandman.agent.a2a;

import edu.kit.kastel.mcse.ardoco.sandman.agent.Telemetry;
import io.opentelemetry.api.common.Attributes;
import io.opentelemetry.api.trace.SpanKind;
import java.net.URI;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicReference;
import org.a2aproject.sdk.A2A;
import org.a2aproject.sdk.client.Client;
import org.a2aproject.sdk.client.TaskEvent;
import org.a2aproject.sdk.client.config.ClientConfig;
import org.a2aproject.sdk.client.http.A2AHttpClient;
import org.a2aproject.sdk.client.transport.jsonrpc.JSONRPCTransport;
import org.a2aproject.sdk.client.transport.jsonrpc.JSONRPCTransportConfig;
import org.a2aproject.sdk.client.transport.spi.interceptors.ClientCallContext;
import org.a2aproject.sdk.spec.AgentCard;
import org.a2aproject.sdk.spec.AgentInterface;
import org.a2aproject.sdk.spec.Artifact;
import org.a2aproject.sdk.spec.CancelTaskParams;
import org.a2aproject.sdk.spec.DataPart;
import org.a2aproject.sdk.spec.Message;
import org.a2aproject.sdk.spec.MessageSendConfiguration;
import org.a2aproject.sdk.spec.MessageSendParams;
import org.a2aproject.sdk.spec.Task;
import org.a2aproject.sdk.spec.TaskQueryParams;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

/** Submits skill tasks (returnImmediately), polls and cancels them, with this service's own token. */
public final class A2aClient implements AutoCloseable {

    /** State of a task as seen by a caller; result and error are null if absent. */
    public record TaskSnapshot(String taskId, String state, JsonNode result, JsonNode error) {
        /** True for COMPLETED, FAILED, CANCELED, REJECTED. */
        public boolean terminal() {
            return List.of("TASK_STATE_COMPLETED", "TASK_STATE_FAILED", "TASK_STATE_CANCELED", "TASK_STATE_REJECTED")
                    .contains(state);
        }
    }

    private final Map<String, String> headers;
    private final Map<String, Client> clients = new ConcurrentHashMap<>();
    private final A2AHttpClient http;

    public A2aClient(String token) {
        this(token, null);
    }

    /** http: the HTTP client for the Agent Card and the calls (for timeouts and size limits), null for the SDK default. */
    public A2aClient(String token, A2AHttpClient http) {
        this.http = http;
        // a2a-java rejects calls that do not list the card's required extension (W-2)
        headers = Map.of(
                "Authorization", "Bearer " + token, "A2A-Extensions", SkillExecutor.EXTENSION, "A2A-Version", "1.0");
    }

    private ClientCallContext callContext() {
        Map<String, String> all = new LinkedHashMap<>(headers);
        Telemetry.inject(all);
        return new ClientCallContext(Map.of(), all);
    }

    private static <T> T clientSpan(String name, Attributes attributes, Telemetry.Body<T, RuntimeException> body) {
        return Telemetry.inSpan(
                Telemetry.span(Telemetry.SPAN_A2A_CLIENT + " " + name, SpanKind.CLIENT, attributes), body);
    }

    /** The agent's public card. */
    public AgentCard card(String agentUrl) {
        return http == null ? A2A.getAgentCard(agentUrl) : A2A.getAgentCard(http, agentUrl);
    }

    private Client client(String agentUrl) {
        return clients.computeIfAbsent(agentUrl, url -> {
            AgentCard card = card(url);
            URI base = URI.create(url);
            // reach the agent at the configured address, keep the card's path
            List<AgentInterface> interfaces = card.supportedInterfaces().stream()
                    .map(i -> new AgentInterface(
                            i.protocolBinding(),
                            base.resolve(URI.create(i.url()).getRawPath()).toString(),
                            i.tenant(),
                            i.protocolVersion()))
                    .toList();
            return Client.builder(AgentCard.builder(card)
                            .supportedInterfaces(interfaces)
                            .build())
                    .clientConfig(new ClientConfig.Builder()
                            .setStreaming(false)
                            .setPolling(true)
                            .build())
                    .withTransport(
                            JSONRPCTransport.class,
                            http == null ? new JSONRPCTransportConfig() : new JSONRPCTransportConfig(http))
                    .build();
        });
    }

    /** Sends the skill message; the caller supplies (and should store) the messageId before sending. */
    public TaskSnapshot submit(
            String agentUrl, String skill, JsonNode input, String workflowId, String actor, String messageId) {
        Map<String, Object> metadata = new LinkedHashMap<>();
        metadata.put(SkillExecutor.SKILL_KEY, skill);
        if (workflowId != null) {
            metadata.put(SkillExecutor.WORKFLOW_KEY, workflowId);
        }
        if (actor != null) {
            metadata.put(SkillExecutor.ACTOR_KEY, actor);
        }
        Message message = Message.builder()
                .messageId(Objects.requireNonNull(messageId, "messageId"))
                .role(Message.Role.ROLE_USER)
                .metadata(metadata)
                .parts(new DataPart(JsonMapper.shared().convertValue(input, Object.class)))
                .build();
        return send(agentUrl, message);
    }

    /** Sends an A2A message with returnImmediately. */
    public TaskSnapshot send(String agentUrl, Message message) {
        var params = MessageSendParams.builder()
                .message(message)
                .configuration(MessageSendConfiguration.builder()
                        .returnImmediately(true)
                        .build())
                .build();
        Object skill = message.metadata() == null ? null : message.metadata().get(SkillExecutor.SKILL_KEY);
        return clientSpan("send", Attributes.of(Telemetry.ATTR_SKILL, String.valueOf(skill)), () -> {
            AtomicReference<Task> task = new AtomicReference<>();
            client(agentUrl)
                    .sendMessage(
                            params,
                            List.of((ev, card) -> {
                                if (ev instanceof TaskEvent te) {
                                    task.set(te.getTask());
                                }
                            }),
                            null,
                            callContext());
            if (task.get() == null) {
                throw new IllegalStateException(agentUrl + " returned no task");
            }
            return snapshot(task.get());
        });
    }

    /** Current state, result and error of a task. */
    public TaskSnapshot get(String agentUrl, String taskId) {
        return clientSpan(
                "get",
                Attributes.of(Telemetry.ATTR_TASK_ID, taskId),
                () -> snapshot(client(agentUrl).getTask(new TaskQueryParams(taskId), callContext())));
    }

    /** Requests cancellation. */
    public TaskSnapshot cancel(String agentUrl, String taskId) {
        return clientSpan(
                "cancel",
                Attributes.of(Telemetry.ATTR_TASK_ID, taskId),
                () -> snapshot(client(agentUrl).cancelTask(new CancelTaskParams(taskId), callContext())));
    }

    static TaskSnapshot snapshot(Task task) {
        JsonNode result = null;
        for (Artifact a : task.artifacts() == null ? List.<Artifact>of() : task.artifacts()) {
            if ("result".equals(a.name()) && !a.parts().isEmpty() && a.parts().get(0) instanceof DataPart d) {
                result = SkillExecutor.toJson(d.data());
            }
        }
        Message status = task.status().message();
        JsonNode error =
                status == null || status.metadata() == null || status.metadata().get(SkillExecutor.ERROR_KEY) == null
                        ? null
                        : SkillExecutor.toJson(status.metadata().get(SkillExecutor.ERROR_KEY));
        return new TaskSnapshot(task.id(), task.status().state().name(), result, error);
    }

    @Override
    public void close() {
        clients.values().forEach(Client::close);
    }
}
