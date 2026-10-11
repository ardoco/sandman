/* Licensed under MIT 2025-2026. */
package edu.kit.kastel.mcse.ardoco.sandman.tlr.lissa.elementstore.strategy;

import edu.kit.kastel.mcse.ardoco.sandman.tlr.lissa.knowledge.Element;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.lissa.utils.Pair;
import java.util.List;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * Interface defining the contract for retrieval strategies used to find similar elements based on their vector representations.
 */
public interface RetrievalStrategy {
    Logger logger = LoggerFactory.getLogger(RetrievalStrategy.class);

    /**
     * Finds similar elements to the given query element based on their vector representations.
     *
     * @param query The query element and its vector representation
     * @param allElementsInStore A list of all elements in the store along with their vector representations
     * @return A list of pairs containing similar elements and their similarity scores, sorted by similarity in descending order
     */
    List<Pair<Element, Float>> findSimilarElements(
            Pair<Element, float[]> query, List<Pair<Element, float[]>> allElementsInStore);
}
