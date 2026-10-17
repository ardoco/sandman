package edu.kit.kastel.mcse.ardoco.sandman.inconsistency.ardoco;

import edu.kit.kastel.mcse.ardoco.core.api.models.Metamodel;
import edu.kit.kastel.mcse.ardoco.core.api.stage.connectiongenerator.ConnectionStates;
import edu.kit.kastel.mcse.ardoco.core.common.similarity.SimilarityUtils;
import edu.kit.kastel.mcse.ardoco.core.common.util.DataRepositoryHelper;
import edu.kit.kastel.mcse.ardoco.core.data.DataRepository;
import edu.kit.kastel.mcse.ardoco.core.pipeline.AbstractPipelineStep;
import edu.kit.kastel.mcse.ardoco.core.pipeline.agent.Claimant;
import edu.kit.kastel.mcse.ardoco.tlr.connectiongenerator.ConnectionStatesImpl;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import org.eclipse.collections.api.map.sorted.ImmutableSortedMap;

/**
 * Replaces the ConnectionStates with RecommendedInstance to model element links derived from sentence-level links
 * (Q-20 workaround, spike T0.12 section 5). A link (sentence s, element m) becomes a link RI to m for every RI with a
 * word in s, if ArDoCo has an instance link from this RI to m or the RI name is similar to m (rule internal+similar).
 */
final class ExternalLinksStep extends AbstractPipelineStep implements Claimant {

    /** Counts of one run: sentence-level links in, RI links added, links without any RI link. */
    static final class Stats {
        int input;
        int mapped;
        int unmapped;

        @Override
        public String toString() {
            return "links in=" + input + ", RI links added=" + mapped + ", links unmapped=" + unmapped;
        }
    }

    private final List<ExternalLink> links;
    private final Stats stats;

    ExternalLinksStep(DataRepository repo, List<ExternalLink> links, Stats stats) {
        super("ExternalLinks", repo);
        this.links = links;
        this.stats = stats;
    }

    @Override
    protected void process() {
        var repo = getDataRepository();
        var models = DataRepositoryHelper.getModelStatesData(repo);
        var recommendations = DataRepositoryHelper.getRecommendationStates(repo);
        var computed = DataRepositoryHelper.getConnectionStates(repo);
        var fresh = ConnectionStatesImpl.build(models.getMetamodels().toArray(Metamodel[]::new));
        Set<ExternalLink> used = new HashSet<>();
        int added = 0;
        for (var metamodel : models.getMetamodels()) {
            var state = fresh.getConnectionState(metamodel);
            var entities = models.getModel(metamodel).getEndpoints();
            var internalLinks = computed.getConnectionState(metamodel).getInstanceLinks();
            for (var ri : recommendations.getRecommendationState(metamodel).getRecommendedInstances()) {
                Set<Integer> riSentences = new HashSet<>();
                ri.getNameMappings().forEach(nm -> nm.getWords().forEach(w -> riSentences.add(w.getSentenceNumber())));
                for (var entity : entities) {
                    List<ExternalLink> hits = links.stream()
                            .filter(l -> l.modelId().equals(entity.getId()) && riSentences.contains(l.sentence()))
                            .toList();
                    if (hits.isEmpty()) {
                        continue;
                    }
                    boolean similar =
                            SimilarityUtils.getInstance().isRecommendedInstanceSimilarToModelInstance(ri, entity);
                    boolean internal =
                            internalLinks.anySatisfy(il -> il.getFirstEndpoint().equals(ri)
                                    && il.getSecondEndpoint().equals(entity));
                    if (similar || internal) {
                        state.addToLinks(ri, entity, this, 1.0);
                        added++;
                        used.addAll(hits);
                    }
                }
            }
        }
        repo.addData(ConnectionStates.ID, fresh);
        stats.input = links.size();
        stats.mapped = added;
        stats.unmapped = (int) links.stream().filter(l -> !used.contains(l)).count();
    }

    @Override
    protected void before() {}

    @Override
    protected void after() {}

    @Override
    protected void delegateApplyConfigurationToInternalObjects(ImmutableSortedMap<String, String> map) {}
}
