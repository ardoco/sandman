/* Licensed under MIT 2025-2026. */
package edu.kit.kastel.mcse.ardoco.sandman.tlr.lissa.classifier;

import edu.kit.kastel.mcse.ardoco.sandman.tlr.lissa.elementstore.SourceElementStore;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.lissa.elementstore.TargetElementStore;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.lissa.knowledge.Element;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.lissa.utils.Pair;
import java.util.ArrayList;
import java.util.Collection;
import java.util.List;
import java.util.Objects;
import java.util.Optional;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicReference;
import java.util.function.BooleanSupplier;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * Abstract base class for trace link classifiers in the LiSSA framework.
 * This class provides the foundation for implementing different classification strategies
 * for identifying trace links between source and target elements. It supports both
 * sequential and parallel processing of classification tasks.
 */
public abstract class Classifier {
    /**
     * Logger instance for logging classifier activities.
     */
    protected final Logger logger = LoggerFactory.getLogger(this.getClass());
    /**
     * The number of threads to use for parallel processing.
     */
    protected final int threads;
    /**
     * Tells whether the run was cancelled; checked before every classification.
     */
    protected final BooleanSupplier cancelled;

    /**
     * Creates a new classifier with the specified number of threads.
     *
     * @param threads The number of threads to use for parallel processing
     * @param cancelled Tells whether the run was cancelled
     */
    protected Classifier(int threads, BooleanSupplier cancelled) {
        this.threads = Math.max(1, threads);
        this.cancelled = Objects.requireNonNull(cancelled);
    }

    /**
     * Classifies trace links between source and target elements.
     * This method can process the classification either sequentially or in parallel
     * depending on the number of threads configured.
     *
     * @param sourceStore The store containing source elements
     * @param targetStore The store containing target elements
     * @return A list of classification results
     */
    public List<ClassificationResult> classify(SourceElementStore sourceStore, TargetElementStore targetStore) {
        return classify(createClassificationTasks(sourceStore, targetStore));
    }

    private List<ClassificationResult> classify(List<Pair<Element, Element>> tasks) {
        if (threads <= 1) {
            return sequentialClassify(tasks);
        }
        return parallelClassify(tasks);
    }

    /**
     * Performs parallel classification of trace links using virtual threads.
     * Each thread processes tasks from a shared queue and adds results to a concurrent collection.
     *
     * @param tasks The list of element pairs to classify
     * @return A list of classification results
     */
    protected final List<ClassificationResult> parallelClassify(List<Pair<Element, Element>> tasks) {
        ConcurrentLinkedQueue<ClassificationResult> results = new ConcurrentLinkedQueue<>();
        ConcurrentLinkedQueue<Pair<Element, Element>> taskQueue = new ConcurrentLinkedQueue<>(tasks);
        AtomicReference<RuntimeException> failure = new AtomicReference<>();

        Thread[] workers = new Thread[threads];
        for (int i = 0; i < threads; i++) {
            workers[i] = Thread.ofVirtual().start(new Runnable() {
                private final Classifier copy = copyOf();

                @Override
                public void run() {
                    while (failure.get() == null && !taskQueue.isEmpty()) {
                        Pair<Element, Element> pair = taskQueue.poll();
                        if (pair == null) {
                            return;
                        }
                        Optional<ClassificationResult> result;
                        try {
                            checkCancelled();
                            result = copy.classify(pair.first(), pair.second());
                        } catch (RuntimeException e) {
                            failure.compareAndSet(null, e);
                            return;
                        }
                        logger.debug(
                                "Classified (P) {} with {}: {}",
                                pair.first().getIdentifier(),
                                pair.second().getIdentifier(),
                                result);
                        result.ifPresent(results::add);
                    }
                }
            });
        }

        logger.debug("Waiting for classification to finish. Tasks in queue: {}", taskQueue.size());

        for (Thread worker : workers) {
            try {
                worker.join();
            } catch (InterruptedException e) {
                logger.error("Worker thread interrupted.", e);
                Thread.currentThread().interrupt();
            }
        }

        if (failure.get() != null) {
            throw failure.get();
        }
        List<ClassificationResult> resultList = new ArrayList<>(results);
        logger.debug("Finished parallel classification with {} results.", resultList.size());
        return resultList;
    }

    private void checkCancelled() {
        if (cancelled.getAsBoolean()) {
            throw new CancellationException("classification cancelled");
        }
    }

    /**
     * Performs sequential classification of trace links.
     * Each element pair is processed one at a time in the current thread.
     *
     * @param tasks The list of element pairs to classify
     * @return A list of classification results
     */
    private List<ClassificationResult> sequentialClassify(List<Pair<Element, Element>> tasks) {
        List<ClassificationResult> results = new ArrayList<>();
        for (var task : tasks) {
            checkCancelled();
            var result = classify(task.first(), task.second());
            logger.debug(
                    "Classified {} with {}: {}",
                    task.first().getIdentifier(),
                    task.second().getIdentifier(),
                    result);
            result.ifPresent(results::add);
        }
        logger.info("Finished sequential classification with {} results.", results.size());
        return results;
    }

    /**
     * Classifies a single classification task.
     * This method delegates to the abstract {@link #classify(Element, Element)} method
     * which must be implemented by concrete classifier subclasses.
     *
     * @param task The classification task containing source and target elements
     * @return     A classification result if a trace link is found, empty otherwise
     */
    public Optional<ClassificationResult> classify(ClassificationTask task) {
        return classify(task.source(), task.target());
    }

    /**
     * Classifies a collection of classification tasks.
     * This method can process the classification either sequentially or in parallel
     * depending on the number of threads configured.
     *
     * @param classificationTasks The collection of classification tasks to classify
     * @return A list of classification results
     */
    public List<ClassificationResult> classify(Collection<ClassificationTask> classificationTasks) {
        return classify(createClassificationTasks(classificationTasks));
    }

    /**
     * Classifies a pair of elements.
     * This method must be implemented by concrete classifier implementations to define
     * their specific classification logic.
     *
     * @param source The source element
     * @param target The target element
     * @return A classification result if a trace link is found, empty otherwise
     */
    protected abstract Optional<ClassificationResult> classify(Element source, Element target);

    /**
     * Creates a copy of this classifier instance.
     * This method is used to create thread-local copies for parallel processing.
     *
     * @return A new instance of the same classifier type
     */
    protected abstract Classifier copyOf();

    /**
     * Creates a list of classification tasks from source and target element stores.
     * Each task represents a pair of elements to be classified.
     *
     * @param sourceStore The store containing source elements
     * @param targetStore The store containing target elements
     * @return A list of element pairs to classify
     */
    protected static List<Pair<Element, Element>> createClassificationTasks(
            SourceElementStore sourceStore, TargetElementStore targetStore) {
        List<Pair<Element, Element>> tasks = new ArrayList<>();

        for (var source : sourceStore.getAllElements(true)) {
            var targetCandidates = targetStore.findSimilar(source);
            for (Element target : targetCandidates) {
                tasks.add(new Pair<>(source.first(), target));
            }
        }
        return tasks;
    }

    private static List<Pair<Element, Element>> createClassificationTasks(
            Collection<ClassificationTask> classificationTasks) {
        List<Pair<Element, Element>> tasks = new ArrayList<>();
        for (ClassificationTask task : classificationTasks) {
            tasks.add(new Pair<>(task.source(), task.target()));
        }
        return tasks;
    }
}
