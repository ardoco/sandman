package edu.kit.kastel.mcse.ardoco.sandman.inconsistency.ardoco;

import dev.langchain4j.agent.tool.ToolExecutionRequest;
import dev.langchain4j.agent.tool.ToolSpecification;
import dev.langchain4j.data.message.AiMessage;
import dev.langchain4j.data.message.ChatMessage;
import dev.langchain4j.data.message.SystemMessage;
import dev.langchain4j.data.message.ToolExecutionResultMessage;
import dev.langchain4j.data.message.UserMessage;
import dev.langchain4j.internal.JsonSchemaElementUtils;
import dev.langchain4j.model.chat.ChatModel;
import dev.langchain4j.model.chat.request.ChatRequest;
import dev.langchain4j.model.chat.response.ChatResponse;
import edu.kit.kastel.mcse.ardoco.sandman.agent.LlmAccess;
import edu.kit.kastel.mcse.ardoco.sandman.agent.LlmClient;
import java.util.ArrayList;
import java.util.List;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ArrayNode;
import tools.jackson.databind.node.ObjectNode;

/**
 * NAER's and the validation agents' chat model over the gateway client: virtual key, routes and policy of
 * {@link LlmAccess}; no provider credential. Requests without tools use the plain text call, so their wire format
 * stays as before.
 */
final class ArtemisChatModel implements ChatModel {

    static final int SEED = 42;

    private final LlmClient client;
    private final String route;

    ArtemisChatModel(LlmClient client, LlmAccess access) {
        this.client = client;
        this.route = access.chatRoute();
    }

    @Override
    public ChatResponse doChat(ChatRequest request) {
        List<ToolSpecification> specs = request.toolSpecifications();
        if (specs == null || specs.isEmpty()) {
            List<LlmClient.Message> messages =
                    request.messages().stream().map(ArtemisChatModel::convert).toList();
            return ChatResponse.builder()
                    .aiMessage(
                            AiMessage.from(client.chat(route, messages, 0, SEED).content()))
                    .build();
        }
        ArrayNode messages = JsonMapper.shared().createArrayNode();
        request.messages().forEach(m -> messages.add(json(m)));
        ArrayNode tools = JsonMapper.shared().createArrayNode();
        specs.forEach(s -> tools.add(json(s)));
        JsonNode answer = client.chatWithTools(route, messages, tools, 0, SEED);
        List<ToolExecutionRequest> calls = new ArrayList<>();
        for (JsonNode c : answer.path("tool_calls")) {
            calls.add(ToolExecutionRequest.builder()
                    .id(c.path("id").asString(""))
                    .name(c.path("function").path("name").asString(""))
                    .arguments(c.path("function").path("arguments").asString("{}"))
                    .build());
        }
        String text = answer.path("content").asString("");
        return ChatResponse.builder()
                .aiMessage(calls.isEmpty() ? AiMessage.from(text) : AiMessage.from(text, calls))
                .build();
    }

    private static LlmClient.Message convert(ChatMessage m) {
        return switch (m) {
            case SystemMessage s -> new LlmClient.Message("system", s.text());
            case UserMessage u -> new LlmClient.Message("user", u.singleText());
            case AiMessage a -> new LlmClient.Message("assistant", a.text());
            default -> throw new IllegalArgumentException("unsupported message type " + m.type());
        };
    }

    private static ObjectNode json(ChatMessage m) {
        ObjectNode out = JsonMapper.shared().createObjectNode();
        switch (m) {
            case SystemMessage s -> out.put("role", "system").put("content", s.text());
            case UserMessage u -> out.put("role", "user").put("content", u.singleText());
            case ToolExecutionResultMessage t ->
                out.put("role", "tool").put("tool_call_id", t.id()).put("content", t.text());
            case AiMessage a -> {
                out.put("role", "assistant");
                if (a.text() == null) {
                    out.putNull("content");
                } else {
                    out.put("content", a.text());
                }
                if (a.hasToolExecutionRequests()) {
                    ArrayNode calls = out.putArray("tool_calls");
                    for (ToolExecutionRequest r : a.toolExecutionRequests()) {
                        ObjectNode call = calls.addObject().put("id", r.id()).put("type", "function");
                        call.putObject("function").put("name", r.name()).put("arguments", r.arguments());
                    }
                }
            }
            default -> throw new IllegalArgumentException("unsupported message type " + m.type());
        }
        return out;
    }

    private static ObjectNode json(ToolSpecification s) {
        ObjectNode fn = JsonMapper.shared().createObjectNode();
        fn.put("name", s.name());
        if (s.description() != null) {
            fn.put("description", s.description());
        }
        fn.set(
                "parameters",
                JsonMapper.shared()
                        .valueToTree(JsonSchemaElementUtils.toMap(
                                s.parameters() == null
                                        ? dev.langchain4j.model.chat.request.json.JsonObjectSchema.builder()
                                                .build()
                                        : s.parameters())));
        ObjectNode out = JsonMapper.shared().createObjectNode();
        out.put("type", "function");
        out.set("function", fn);
        return out;
    }
}
