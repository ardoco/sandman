/* Licensed under MIT 2025-2026. */
package edu.kit.kastel.mcse.ardoco.sandman.tlr.lissa.classifier;

import edu.kit.kastel.mcse.ardoco.sandman.agent.LlmClient;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.lissa.knowledge.Element;
import java.util.ArrayList;
import java.util.List;
import java.util.Optional;
import java.util.function.BooleanSupplier;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * A classifier that uses a language model to reason about trace links between elements.
 * The model answers with a trace tag that contains "yes" or "no".
 */
public class ReasoningClassifier extends Classifier {
    /**
     * The identifier used for this classifier type in configuration.
     */
    public static final String REASONING_CLASSIFIER_NAME = "reasoning";

    private static final String SYSTEM_MESSAGE =
            "Your job is to determine if there is a traceability link between two artifacts of a system.";

    private final LissaChat chat;
    private final String prompt;

    /**
     * Creates a new reasoning classifier.
     *
     * @param threads The number of threads to use for parallel processing
     * @param cancelled Tells whether the run was cancelled
     * @param chat The chat call used for classification
     * @param prompt The prompt template to use
     */
    public ReasoningClassifier(int threads, BooleanSupplier cancelled, LissaChat chat, String prompt) {
        super(threads, cancelled);
        this.chat = chat;
        this.prompt = prompt;
    }

    @Override
    protected final Classifier copyOf() {
        return new ReasoningClassifier(threads, cancelled, chat, prompt);
    }

    @Override
    protected final Optional<ClassificationResult> classify(Element source, Element target) {
        String llmResponse = classifyIntern(source, target);
        if (isRelated(llmResponse)) {
            return Optional.of(ClassificationResult.of(source, target));
        }
        return Optional.empty();
    }

    /**
     * Determines if the language model's response indicates a trace link.
     * The response is expected to contain a trace tag with "yes" or "no".
     *
     * @param llmResponse The response from the language model
     * @return true if the response indicates a trace link, false otherwise
     */
    private boolean isRelated(String llmResponse) {
        Pattern pattern = Pattern.compile("<trace>(.*?)</trace>", Pattern.CASE_INSENSITIVE);
        Matcher matcher = pattern.matcher(llmResponse);
        boolean related = false;
        if (matcher.find()) {
            related = matcher.group().toLowerCase().contains("yes");
        } else {
            logger.debug("No trace tag found in response: {}", llmResponse);
        }
        return related;
    }

    private String classifyIntern(Element source, Element target) {
        List<LlmClient.Message> messages = new ArrayList<>();
        messages.add(new LlmClient.Message("system", SYSTEM_MESSAGE));
        String request = prompt.replace("{source_type}", source.getType())
                .replace("{source_content}", source.getContent())
                .replace("{target_type}", target.getType())
                .replace("{target_content}", target.getContent());
        messages.add(new LlmClient.Message("user", request));
        logger.debug("Classifying: {} and {}", source.getIdentifier(), target.getIdentifier());
        return chat.chat(messages);
    }
}
