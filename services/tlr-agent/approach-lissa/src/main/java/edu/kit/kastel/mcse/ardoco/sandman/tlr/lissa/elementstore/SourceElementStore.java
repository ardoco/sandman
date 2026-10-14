/* Licensed under MIT 2025-2026. */
package edu.kit.kastel.mcse.ardoco.sandman.tlr.lissa.elementstore;

import edu.kit.kastel.mcse.ardoco.sandman.tlr.lissa.knowledge.Element;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.lissa.utils.Pair;
import java.util.List;

/**
 * A store for source elements and their embeddings in the LiSSA framework.
 * Providing functionality for element retrieval and filtering
 * <b>Source Store</b> (no similarity search):
 * <ul>
 *      <li>Used to store source elements that will be used as queries in LiSSA's classification phase</li>
 *      <li>Does not support similarity search as it's unnecessary for source elements</li>
 *      <li>Can retrieve all elements at once for LiSSA's batch processing</li>
 *      <li>Supports filtering elements by comparison flag for LiSSA's selective analysis</li>
 * </ul>
 */
public class SourceElementStore extends ElementStore {

    /** Creates an empty source store. */
    public SourceElementStore() {
        super();
    }

    /**
     * Creates a new source element store with the given content.
     *
     * @param content List of pairs containing elements and their embeddings
     */
    public SourceElementStore(List<Pair<Element, float[]>> content) {
        super(content);
    }

    /**
     * Retrieves all elements in the store for LiSSA's batch processing.
     * Defaults to retrieving all elements, regardless of comparison flag.
     *
     * @return List of all elements
     */
    public List<Element> getAllElements() {
        return getAllElementsIntern(false).stream().map(Pair::first).toList();
    }

    /**
     * Retrieves all elements in the store for LiSSA's batch processing.
     *
     * @param onlyCompare If true, only returns elements marked for comparison
     * @return List of pairs containing elements and their embeddings
     */
    public List<Pair<Element, float[]>> getAllElements(boolean onlyCompare) {
        return getAllElementsIntern(onlyCompare);
    }
}
