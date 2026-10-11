/* Licensed under MIT 2025-2026. */
package edu.kit.kastel.mcse.ardoco.sandman.tlr.lissa.classifier;

import edu.kit.kastel.mcse.ardoco.sandman.agent.LlmClient;
import java.util.List;

/**
 * The chat call of a classifier: one {@link LlmClient}, route, temperature and seed.
 * Replaces the LangChain4j chat model of LiSSA.
 */
public record LissaChat(LlmClient client, String route, double temperature, Integer seed) {

    public String chat(List<LlmClient.Message> messages) {
        return client.chat(route, messages, temperature, seed).content();
    }
}
