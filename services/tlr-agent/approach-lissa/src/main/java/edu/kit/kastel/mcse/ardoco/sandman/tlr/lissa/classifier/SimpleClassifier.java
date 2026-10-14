/* Licensed under MIT 2025-2026. */
package edu.kit.kastel.mcse.ardoco.sandman.tlr.lissa.classifier;

import edu.kit.kastel.mcse.ardoco.sandman.agent.LlmClient;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.lissa.knowledge.Element;
import java.util.List;
import java.util.Optional;
import java.util.function.BooleanSupplier;

/**
 * A simple classifier that uses a language model to determine trace links between elements.
 * This classifier uses a straightforward yes/no approach, asking the language model
 * directly whether elements are related.
 */
public class SimpleClassifier extends Classifier {

    /**
     * The default template for classification requests.
     * This template presents two artifacts and asks if they are related.
     */
    public static final String DEFAULT_TEMPLATE = """
            Question: Here are two parts of software development artifacts.

            {source_type}: '''{source_content}'''

            {target_type}: '''{target_content}'''
            Are they related?

            Answer with 'yes' or 'no'.
            """;

    /**
     * The identifier used for this classifier type in configuration.
     */
    public static final String SIMPLE_CLASSIFIER_NAME = "simple";

    private final LissaChat chat;
    private final String template;

    /**
     * Creates a new simple classifier.
     *
     * @param threads The number of threads to use for parallel processing
     * @param cancelled Tells whether the run was cancelled
     * @param chat The chat call used for classification
     * @param template The template to use for classification requests
     */
    public SimpleClassifier(int threads, BooleanSupplier cancelled, LissaChat chat, String template) {
        super(threads, cancelled);
        this.chat = chat;
        this.template = template;
    }

    @Override
    protected final Classifier copyOf() {
        return new SimpleClassifier(threads, cancelled, chat, template);
    }

    /**
     * Classifies a pair of elements by using the language model to determine if they are related.
     *
     * @param source The source element
     * @param target The target element
     * @return A classification result if the elements are related, empty otherwise
     */
    @Override
    protected final Optional<ClassificationResult> classify(Element source, Element target) {
        String llmResponse = classifyIntern(source, target);

        String thinkEnd = "</think>";
        if (llmResponse.startsWith("<think>") && llmResponse.contains(thinkEnd)) {
            // Omit the thinking of models like deepseek-r1
            llmResponse = llmResponse
                    .substring(llmResponse.indexOf(thinkEnd) + thinkEnd.length())
                    .strip();
        }

        boolean isRelated = llmResponse.toLowerCase().contains("yes");
        if (isRelated) {
            return Optional.of(ClassificationResult.of(source, target));
        }

        return Optional.empty();
    }

    private String classifyIntern(Element source, Element target) {
        String request = template.replace("{source_type}", source.getType())
                .replace("{source_content}", source.getContent())
                .replace("{target_type}", target.getType())
                .replace("{target_content}", target.getContent());
        logger.debug("Classifying: {} and {}", source.getIdentifier(), target.getIdentifier());
        return chat.chat(List.of(new LlmClient.Message("user", request)));
    }
}
