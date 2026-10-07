package edu.kit.kastel.mcse.ardoco.sandman.spike;

import java.net.URI;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.atomic.AtomicReference;

import org.a2aproject.sdk.A2A;
import org.a2aproject.sdk.client.Client;
import org.a2aproject.sdk.client.TaskEvent;
import org.a2aproject.sdk.client.config.ClientConfig;
import org.a2aproject.sdk.client.transport.jsonrpc.JSONRPCTransport;
import org.a2aproject.sdk.client.transport.jsonrpc.JSONRPCTransportConfig;
import org.a2aproject.sdk.client.transport.spi.interceptors.ClientCallContext;
import org.a2aproject.sdk.jsonrpc.common.json.JsonUtil;
import org.a2aproject.sdk.spec.AgentCard;
import org.a2aproject.sdk.spec.AgentInterface;
import org.a2aproject.sdk.spec.CancelTaskParams;
import org.a2aproject.sdk.spec.DataPart;
import org.a2aproject.sdk.spec.Message;
import org.a2aproject.sdk.spec.MessageSendConfiguration;
import org.a2aproject.sdk.spec.MessageSendParams;
import org.a2aproject.sdk.spec.Task;
import org.a2aproject.sdk.spec.TaskQueryParams;
import tools.jackson.databind.json.JsonMapper;

/** Java a2a client against any SANDMAN agent: vectors.json skillInvocation cases, a valid call, GetTask unknown id, CancelTask. */
public class ClientCli {

    public static void main(String[] args) throws Exception {
        String base = args[0];
        String token = System.getenv().getOrDefault("SPIKE_TOKEN", "spike-token");
        AgentCard card = A2A.getAgentCard(base);
        System.out.println("CARD " + JsonUtil.toJson(card));
        var iface = card.supportedInterfaces().get(0);
        String url = base.replaceAll("/$", "") + URI.create(iface.url()).getPath(); // reach the agent at the configured address
        card = AgentCard.builder(card)
                .supportedInterfaces(List.of(new AgentInterface(iface.protocolBinding(), url, iface.tenant(), iface.protocolVersion())))
                .build();
        Client client = Client.builder(card)
                .clientConfig(new ClientConfig.Builder().setStreaming(false).setPolling(true).build())
                .withTransport(JSONRPCTransport.class, new JSONRPCTransportConfig())
                .build();
        // a2a-java rejects every call unless required card extensions are listed in A2A-Extensions (spec §3.6.2).
        var ctx = new ClientCallContext(Map.of(), Map.of("Authorization", "Bearer " + token, "A2A-Extensions", Agent.EXT));

        List<Map<String, Object>> cases = new ArrayList<>();
        Map<String, Object> vectors = JsonMapper.shared().readValue(Path.of(Agent.CONTRACTS.toString(), "conformance/vectors.json").toFile(), Map.class);
        for (Object c : (List<?>) ((Map<?, ?>) vectors.get("skillInvocation")).get("cases")) {
            cases.add(Agent.cast(c));
        }
        for (var c : cases) {
            Task t = run(client, ctx, Agent.cast(c.get("message")));
            System.out.printf("CASE %-40s expected=%s/%s got=%s%n", c.get("name"), c.get("expectedState"), c.get("expectedErrorCode"), snap(t));
        }
        var input = new LinkedHashMap<String, Object>(Map.of("projectId", "mediastore",
                "sourceVersionIds", List.of("ver_0123456789abcdef0123456789abcdef", "ver_1123456789abcdef0123456789abcdef"),
                "targetVersionIds", List.of("ver_fedcba9876543210fedcba9876543210")));
        if (args.length > 1) {
            input.put("approaches", List.of(args[1]));
        }
        System.out.println("VALID " + snap(run(client, ctx, msg(input))));

        try {
            client.getTask(new TaskQueryParams("does-not-exist"), ctx);
        } catch (Exception e) {
            System.out.println("GETTASK unknown -> " + e.getClass().getName() + ": " + e.getMessage()
                    + (e.getCause() == null ? "" : " cause=" + e.getCause().getClass().getName()));
        }

        input.put("params", Map.of("sleepMs", 5000));
        Task submitted = send(client, ctx, msg(input));
        System.out.println("CANCEL submitted " + snap(submitted));
        Task cancelled = client.cancelTask(new CancelTaskParams(submitted.id()), ctx);
        System.out.println("CANCEL response " + snap(cancelled));
        Thread.sleep(300);
        System.out.println("CANCEL get " + snap(client.getTask(new TaskQueryParams(submitted.id()), ctx)));
        client.close();
    }

    static Map<String, Object> msg(Map<String, Object> input) {
        return Map.of("messageId", UUID.randomUUID().toString(), "role", "ROLE_USER",
                "metadata", Map.of("sandman.skill", "recover-trace-links"), "parts", List.of(Map.of("data", input)));
    }

    static Task send(Client client, ClientCallContext ctx, Map<String, Object> m) throws Exception {
        var message = Message.builder().messageId((String) m.get("messageId")).role(Message.Role.ROLE_USER)
                .metadata(Agent.cast(m.get("metadata")))
                .parts(new DataPart(((Map<?, ?>) ((List<?>) m.get("parts")).get(0)).get("data")))
                .build();
        var params = MessageSendParams.builder().message(message)
                .configuration(MessageSendConfiguration.builder().returnImmediately(true).build()).build();
        AtomicReference<Task> task = new AtomicReference<>();
        client.sendMessage(params, List.of((ev, c) -> task.set(((TaskEvent) ev).getTask())), null, ctx);
        return task.get();
    }

    static Task run(Client client, ClientCallContext ctx, Map<String, Object> m) throws Exception {
        Task t = send(client, ctx, m);
        for (int i = 0; i < 300 && !t.status().state().isFinal(); i++) {
            Thread.sleep(100);
            t = client.getTask(new TaskQueryParams(t.id()), ctx);
        }
        return t;
    }

    static String snap(Task t) throws Exception {
        Object error = t.status().message() == null || t.status().message().metadata() == null ? null
                : t.status().message().metadata().get("sandman.error");
        Object result = t.artifacts() == null ? null : t.artifacts().stream().filter(a -> "result".equals(a.name()))
                .map(a -> ((DataPart) a.parts().get(0)).data()).findFirst().orElse(null);
        return t.status().state() + " error=" + error + " result=" + result + " (raw number type: "
                + (result == null ? "-" : findNumberType(result)) + ")";
    }

    static String findNumberType(Object o) {
        if (o instanceof Number n) {
            return n.getClass().getSimpleName();
        }
        if (o instanceof Map<?, ?> m) {
            for (Object v : m.values()) {
                String s = findNumberType(v);
                if (!s.isEmpty()) return s;
            }
        }
        if (o instanceof List<?> l) {
            for (Object v : l) {
                String s = findNumberType(v);
                if (!s.isEmpty()) return s;
            }
        }
        return "";
    }
}
