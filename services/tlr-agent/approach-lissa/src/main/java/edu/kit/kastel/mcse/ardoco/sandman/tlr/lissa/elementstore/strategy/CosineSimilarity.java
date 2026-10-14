/* Licensed under MIT 2025-2026. */
package edu.kit.kastel.mcse.ardoco.sandman.tlr.lissa.elementstore.strategy;

import edu.kit.kastel.mcse.ardoco.sandman.tlr.lissa.knowledge.Element;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.lissa.utils.Pair;
import java.util.ArrayList;
import java.util.List;

/**
 * A retrieval strategy that computes the cosine similarity between a query vector and
 * vectors of elements in the store. This strategy is used for finding similar elements
 * based on their vector embeddings.
 * It supports a configurable maximum number of results to return.
 */
public class CosineSimilarity implements RetrievalStrategy {
    /**
     * Special value for the maximum number of results that indicates no limit.
     * Only applicable for target stores in LiSSA's similarity search.
     */
    public static final String MAX_RESULTS_INFINITY_ARGUMENT = "infinity";

    private final int maxResults;

    /**
     * Constructs a CosineSimilarity retrieval strategy.
     *
     * @param maxResults The maximum number of results per query, at least 1
     */
    public CosineSimilarity(int maxResults) {
        if (maxResults < 1) {
            throw new IllegalArgumentException("The maximum number of results must be greater than 0.");
        }
        this.maxResults = maxResults;
    }

    @Override
    public List<Pair<Element, Float>> findSimilarElements(
            Pair<Element, float[]> query, List<Pair<Element, float[]>> allElementsInStore) {
        List<Pair<Element, Float>> similarElements = new ArrayList<>();
        for (var element : allElementsInStore) {
            float[] elementVector = element.second();
            float similarity = cosineSimilarity(query.second(), elementVector);
            similarElements.add(new Pair<>(element.first(), similarity));
        }
        similarElements.sort((a, b) -> Float.compare(b.second(), a.second()));
        return similarElements.subList(0, Math.min(maxResults, similarElements.size()));
    }

    private float cosineSimilarity(float[] queryVector, float[] elementVector) {
        if (queryVector.length != elementVector.length) {
            throw new IllegalArgumentException("The length of the query vector and the element vector must be equal.");
        }

        double dotProduct = 0.0;
        double normA = 0.0;
        double normB = 0.0;
        for (int i = 0; i < queryVector.length; i++) {
            dotProduct += queryVector[i] * elementVector[i];
            normA += Math.pow(queryVector[i], 2);
            normB += Math.pow(elementVector[i], 2);
        }
        return (float) (dotProduct / (Math.sqrt(normA) * Math.sqrt(normB)));
    }
}
