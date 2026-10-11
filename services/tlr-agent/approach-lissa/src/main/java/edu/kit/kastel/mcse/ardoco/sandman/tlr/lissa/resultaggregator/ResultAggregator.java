/* Licensed under MIT 2025. */
package edu.kit.kastel.mcse.ardoco.sandman.tlr.lissa.resultaggregator;

import edu.kit.kastel.mcse.ardoco.sandman.tlr.lissa.classifier.ClassificationResult;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.lissa.knowledge.Element;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.lissa.knowledge.TraceLink;
import java.util.List;
import java.util.Set;

/**
 * Abstract base class for result aggregators that combine classification results into trace links.
 * Result aggregators are responsible for:
 * <ul>
 *     <li>Taking classification results from multiple elements</li>
 *     <li>Combining these results based on specific rules</li>
 *     <li>Creating trace links between source and target elements</li>
 * </ul>
 * The class supports various types of aggregators:
 * <ul>
 *     <li>any_connection: Creates trace links when any element in a group has a positive classification</li>
 * </ul>
 *
 * Each aggregator type implements its own strategy for combining classification results
 * and creating trace links between source and target elements.
 */
public abstract class ResultAggregator {
    /**
     * Aggregates classification results into a set of trace links.
     * This method:
     * <ol>
     *     <li>Takes lists of source and target elements</li>
     *     <li>Processes the classification results for these elements</li>
     *     <li>Applies the aggregator's specific rules to combine results</li>
     *     <li>Creates trace links between elements based on the combined results</li>
     * </ol>
     *
     * @param sourceElements The list of source elements
     * @param targetElements The list of target elements
     * @param classificationResults The classification results to aggregate
     * @return A set of trace links created from the aggregated results
     */
    public abstract Set<TraceLink> aggregate(
            List<Element> sourceElements,
            List<Element> targetElements,
            List<ClassificationResult> classificationResults);
}
