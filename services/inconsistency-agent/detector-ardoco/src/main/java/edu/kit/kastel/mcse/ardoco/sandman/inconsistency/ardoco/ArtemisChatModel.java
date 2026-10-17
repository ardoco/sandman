package edu.kit.kastel.mcse.ardoco.sandman.inconsistency.ardoco;

import dev.langchain4j.data.message.AiMessage;
import dev.langchain4j.data.message.ChatMessage;
import dev.langchain4j.data.message.SystemMessage;
import dev.langchain4j.data.message.UserMessage;
import dev.langchain4j.model.chat.ChatModel;
import dev.langchain4j.model.chat.request.ChatRequest;
import dev.langchain4j.model.chat.response.ChatResponse;
import edu.kit.kastel.mcse.ardoco.sandman.agent.LlmAccess;
import edu.kit.kastel.mcse.ardoco.sandman.agent.LlmClient;
import java.util.List;

/** NAER's chat model over the gateway client: virtual key, routes and policy of {@link LlmAccess}; no provider credential. */
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
        List<LlmClient.Message> messages =
                request.messages().stream().map(ArtemisChatModel::convert).toList();
        return ChatResponse.builder()
                .aiMessage(AiMessage.from(client.chat(route, messages, 0, SEED).content()))
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
}
