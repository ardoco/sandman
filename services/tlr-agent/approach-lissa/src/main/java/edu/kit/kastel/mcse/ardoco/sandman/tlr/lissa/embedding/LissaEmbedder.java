/* Licensed under MIT 2025-2026. */
package edu.kit.kastel.mcse.ardoco.sandman.tlr.lissa.embedding;

import edu.kit.kastel.mcse.ardoco.sandman.agent.LlmCallException;
import edu.kit.kastel.mcse.ardoco.sandman.agent.LlmClient;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.lissa.knowledge.Element;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.lissa.utils.Pair;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.CancellationException;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.Semaphore;
import java.util.function.BooleanSupplier;
import java.util.function.Consumer;

/**
 * Embeds elements with one {@link LlmClient#embed} call per element.
 * An element whose text is over the embedding limit of the model is skipped with a warning.
 */
public final class LissaEmbedder {

    private final LlmClient client;
    private final String route;
    private final int concurrency;
    private final BooleanSupplier cancelled;
    private final Consumer<String> warn;

    public LissaEmbedder(
            LlmClient client, String route, int concurrency, BooleanSupplier cancelled, Consumer<String> warn) {
        this.client = client;
        this.route = route;
        this.concurrency = Math.max(1, concurrency);
        this.cancelled = cancelled;
        this.warn = warn;
    }

    /**
     * Embeds all elements in the order of the input.
     *
     * @param elements The elements to embed
     * @return The embedded elements without the skipped ones
     */
    public List<Pair<Element, float[]>> embed(List<Element> elements) {
        Semaphore permits = new Semaphore(concurrency);
        List<Future<float[]>> futures = new ArrayList<>();
        try (ExecutorService pool = Executors.newVirtualThreadPerTaskExecutor()) {
            for (Element element : elements) {
                futures.add(pool.submit(() -> {
                    permits.acquire();
                    try {
                        if (cancelled.getAsBoolean()) {
                            throw new CancellationException("embedding cancelled");
                        }
                        return client.embed(route, element.getContent());
                    } finally {
                        permits.release();
                    }
                }));
            }
            List<Pair<Element, float[]>> result = new ArrayList<>();
            for (int i = 0; i < elements.size(); i++) {
                float[] vector = await(futures.get(i), elements.get(i));
                if (vector != null) {
                    result.add(new Pair<>(elements.get(i), vector));
                }
            }
            return result;
        }
    }

    private float[] await(Future<float[]> future, Element element) {
        try {
            return future.get();
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new CancellationException("embedding interrupted");
        } catch (ExecutionException e) {
            if (e.getCause() instanceof LlmCallException call && isOverLimit(call)) {
                warn.accept("Skipped " + element.getIdentifier() + ": text is over the embedding limit");
                return null;
            }
            if (e.getCause() instanceof RuntimeException runtime) {
                throw runtime;
            }
            throw new IllegalStateException(e.getCause());
        }
    }

    private static boolean isOverLimit(LlmCallException e) {
        String message = e.getMessage() == null ? "" : e.getMessage();
        return message.contains("context_length_exceeded") || message.contains("maximum context length");
    }
}
