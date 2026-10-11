/* Licensed under MIT 2025. */
package edu.kit.kastel.mcse.ardoco.sandman.tlr.lissa.resultaggregator;

import edu.kit.kastel.mcse.ardoco.sandman.tlr.lissa.classifier.ClassificationResult;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.lissa.knowledge.Element;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.lissa.knowledge.TraceLink;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;

/**
 * A result aggregator that creates trace links when any element in a group has a positive classification.
 * This aggregator:
 * <ul>
 *     <li>Supports different granularity levels for source and target elements</li>
 *     <li>Handles hierarchical relationships between elements</li>
 *     <li>Creates trace links between elements at the specified granularity levels</li>
 * </ul>
 *
 * The aggregator works by:
 * <ol>
 *     <li>Taking classification results between elements</li>
 *     <li>Finding elements at the desired granularity levels</li>
 *     <li>Creating trace links between all valid combinations of source and target elements</li>
 * </ol>
 *
 * Configuration options:
 * <ul>
 *     <li>source_granularity: The granularity level for source elements (default: 0)</li>
 *     <li>target_granularity: The granularity level for target elements (default: 0)</li>
 * </ul>
 *
 * When handling elements at different granularity levels, the aggregator:
 * <ul>
 *     <li>For elements at a lower granularity than desired: Finds all children at the desired level</li>
 *     <li>For elements at a higher granularity than desired: Finds the parent at the desired level</li>
 *     <li>For elements at the desired granularity: Uses them directly</li>
 * </ul>
 */
public class AnyResultAggregator extends ResultAggregator {
    /** The desired granularity level for source elements */
    private final int sourceGranularity;
    /** The desired granularity level for target elements */
    private final int targetGranularity;

    /** Creates an aggregator for source and target elements at granularity 0 (the published r2c setup). */
    public AnyResultAggregator() {
        this.sourceGranularity = 0;
        this.targetGranularity = 0;
    }

    /**
     * Aggregates classification results into trace links, considering granularity levels.
     * This method:
     * <ol>
     *     <li>Processes each classification result</li>
     *     <li>Finds valid source elements at the source granularity level</li>
     *     <li>Finds valid target elements at the target granularity level</li>
     *     <li>Creates trace links between all valid combinations</li>
     * </ol>
     *
     * @param sourceElements The list of all source elements
     * @param targetElements The list of all target elements
     * @param classificationResults The classification results to aggregate
     * @return A set of trace links between elements at the specified granularity levels
     */
    @Override
    public Set<TraceLink> aggregate(
            List<Element> sourceElements,
            List<Element> targetElements,
            List<ClassificationResult> classificationResults) {
        Set<TraceLink> traceLinks = new LinkedHashSet<>();
        for (var result : classificationResults) {
            var sourceElementsForTraceLink =
                    buildListOfValidElements(result.source(), sourceGranularity, sourceElements);
            var target = result.target();
            var targetElementsForTraceLink = buildListOfValidElements(target, targetGranularity, targetElements);
            for (var sourceElement : sourceElementsForTraceLink) {
                for (var targetElement : targetElementsForTraceLink) {
                    traceLinks.add(new TraceLink(sourceElement.getIdentifier(), targetElement.getIdentifier()));
                }
            }
        }
        return traceLinks;
    }

    /**
     * Builds a list of valid elements at the desired granularity level.
     * This method handles three cases:
     * <ol>
     *     <li>Element is at the desired granularity: Returns the element itself</li>
     *     <li>Element is at a lower granularity: Returns all children at the desired level</li>
     *     <li>Element is at a higher granularity: Returns the parent at the desired level</li>
     * </ol>
     *
     * @param element The element to find valid elements for
     * @param desiredGranularity The desired granularity level
     * @param allElements The list of all available elements
     * @return A list of valid elements at the desired granularity level
     */
    private static List<Element> buildListOfValidElements(
            Element element, int desiredGranularity, List<Element> allElements) {
        if (element.getGranularity() == desiredGranularity) {
            return List.of(element);
        }

        if (element.getGranularity() < desiredGranularity) {
            // Element is more course grained than the desired granularity -> find all children that are on the desired
            // granularity
            List<Element> possibleChildren = allElements.stream()
                    .filter(it -> it.getGranularity() == desiredGranularity)
                    .toList();
            // Filter all children that are not transitive children of the element
            return possibleChildren.stream()
                    .filter(it -> isTransitiveChildOf(it, element))
                    .toList();
        }

        // Element is more fine-grained than the desired granularity -> find all parents that are on the desired
        // granularity
        List<Element> possibleParents = allElements.stream()
                .filter(it -> it.getGranularity() == desiredGranularity)
                .toList();
        // Filter all parents that are not transitive parents of the element
        List<Element> validParents = possibleParents.stream()
                .filter(it -> isTransitiveChildOf(element, it))
                .toList();
        assert validParents.size() <= 1;
        return validParents;
    }

    /**
     * Checks if an element is a transitive child of another element.
     * This method traverses the parent chain of the possible child element
     * to determine if the parent element is in its ancestry.
     *
     * @param possibleChild The element to check
     * @param parent The potential parent element
     * @return true if the possible child is a transitive child of the parent
     */
    private static boolean isTransitiveChildOf(Element possibleChild, Element parent) {
        Element currentElement = possibleChild;
        while (currentElement != null) {
            if (parent == currentElement) {
                return true;
            }
            currentElement = currentElement.getParent();
        }
        return false;
    }
}
