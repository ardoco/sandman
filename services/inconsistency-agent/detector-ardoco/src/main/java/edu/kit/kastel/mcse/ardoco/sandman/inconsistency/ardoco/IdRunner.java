package edu.kit.kastel.mcse.ardoco.sandman.inconsistency.ardoco;

import edu.kit.kastel.mcse.ardoco.core.api.PreprocessingData;
import edu.kit.kastel.mcse.ardoco.core.api.models.Metamodel;
import edu.kit.kastel.mcse.ardoco.core.api.models.ModelFormat;
import edu.kit.kastel.mcse.ardoco.core.api.output.ArdocoResult;
import edu.kit.kastel.mcse.ardoco.core.api.stage.inconsistency.Inconsistency;
import edu.kit.kastel.mcse.ardoco.core.api.stage.inconsistency.ModelInconsistency;
import edu.kit.kastel.mcse.ardoco.core.api.stage.inconsistency.TextInconsistency;
import edu.kit.kastel.mcse.ardoco.core.api.text.Text;
import edu.kit.kastel.mcse.ardoco.core.common.util.DataRepositoryHelper;
import edu.kit.kastel.mcse.ardoco.core.execution.Ardoco;
import edu.kit.kastel.mcse.ardoco.id.InconsistencyChecker;
import edu.kit.kastel.mcse.ardoco.id.execution.runner.InconsistencyDetection;
import edu.kit.kastel.mcse.ardoco.id.types.TextEntityAbsentFromModelInconsistency;
import edu.kit.kastel.mcse.ardoco.tlr.connectiongenerator.ConnectionGenerator;
import edu.kit.kastel.mcse.ardoco.tlr.models.agents.ArchitectureConfiguration;
import edu.kit.kastel.mcse.ardoco.tlr.models.agents.ModelProviderAgent;
import edu.kit.kastel.mcse.ardoco.tlr.recommendationgenerator.RecommendationGenerator;
import edu.kit.kastel.mcse.ardoco.tlr.text.providers.TextPreprocessingAgent;
import edu.kit.kastel.mcse.ardoco.tlr.textextraction.TextExtraction;
import java.io.File;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.TreeSet;
import java.util.concurrent.locks.ReentrantLock;
import org.eclipse.collections.api.factory.SortedMaps;
import org.eclipse.collections.api.map.sorted.ImmutableSortedMap;

/** ArDoCo inconsistency detection pipeline from the public factories (the runner's definePipeline is private). Entry points for T3.3. */
final class IdRunner {

    private static final ImmutableSortedMap<String, String> NO_CONFIG = SortedMaps.immutable.empty();

    /** ArDoCo keeps JVM-wide singletons (ConfigManager, SimilarityUtils): one run at a time. */
    static final ReentrantLock LOCK = new ReentrantLock();

    /** What the comparison of two runs looks at: model ids, and (1-based sentence, word) pairs. */
    record NormalForm(TreeSet<String> modelIds, TreeSet<String> textFindings) {}

    private IdRunner() {}

    /** Raw ArDoCo inconsistencies of one run. {@code links == null}: no ExternalLinksStep (the pure ArDoCo pipeline). */
    static List<Inconsistency> run(Text preprocessed, Path model, ModelFormat format, List<ExternalLink> links) {
        return run(preprocessed, model, format, links, new ExternalLinksStep.Stats());
    }

    static List<Inconsistency> run(
            Text preprocessed,
            Path model,
            ModelFormat format,
            List<ExternalLink> links,
            ExternalLinksStep.Stats stats) {
        var ardoco = new Ardoco("sandman-id");
        var repo = ardoco.getDataRepository();
        DataRepositoryHelper.putPreprocessingData(repo, new PreprocessingData(preprocessed));
        ardoco.addPipelineStep(TextPreprocessingAgent.get(NO_CONFIG, repo));
        ardoco.addPipelineStep(ModelProviderAgent.getModelProviderAgent(
                repo,
                NO_CONFIG,
                new ArchitectureConfiguration(
                        model.toFile(), format, Metamodel.ARCHITECTURE_WITH_COMPONENTS_AND_INTERFACES),
                null));
        ardoco.addPipelineStep(TextExtraction.get(NO_CONFIG, repo));
        ardoco.addPipelineStep(RecommendationGenerator.get(NO_CONFIG, repo));
        ardoco.addPipelineStep(ConnectionGenerator.get(NO_CONFIG, repo));
        if (links != null) {
            ardoco.addPipelineStep(new ExternalLinksStep(repo, links, stats));
        }
        ardoco.addPipelineStep(InconsistencyChecker.get(NO_CONFIG, repo));
        ardoco.run();
        return list(new ArdocoResult(repo));
    }

    /**
     * The standalone ArDoCo runner (InconsistencyDetection). The text is injected as PreprocessingData: the ArDoCo
     * CoreNLP models are not on the class path (the provider service annotates, T2.5).
     */
    static ArdocoResult standalone(Text preprocessed, Path rawText, Path model, ModelFormat format, Path outDir) {
        var runner = new InconsistencyDetection("sandman-standalone");
        runner.setUp(rawText.toFile(), model.toFile(), format, NO_CONFIG, new File(outDir.toString()));
        DataRepositoryHelper.putPreprocessingData(
                runner.getArdoco().getDataRepository(), new PreprocessingData(preprocessed));
        return runner.run();
    }

    static List<Inconsistency> inconsistencies(ArdocoResult result) {
        return list(result);
    }

    /** ArDoCo's own sentence-level links (0-based sentence, model id): what an approach would have stored. */
    static List<ExternalLink> selfLinks(ArdocoResult result) {
        return result.getArchitectureTraceLinks().stream()
                .map(tl -> new ExternalLink(
                        tl.getFirstEndpoint().getSentence().getSentenceNumber(),
                        tl.getSecondEndpoint().getId()))
                .distinct()
                .toList();
    }

    private static List<Inconsistency> list(ArdocoResult result) {
        return new ArrayList<>(result.getAllInconsistencies().castToList());
    }

    static Mapping.Raw extract(List<Inconsistency> all) {
        List<Mapping.ModelHit> models = new ArrayList<>();
        List<Mapping.WordHit> words = new ArrayList<>();
        for (Inconsistency i : all) {
            if (i instanceof ModelInconsistency m) {
                String[] row = m.toFileOutput().getFirst();
                models.add(new Mapping.ModelHit(m.getModelInstanceUid(), row[1], row[2]));
            } else if (i instanceof TextEntityAbsentFromModelInconsistency t) {
                words.add(new Mapping.WordHit(t.getSentenceNumber(), t.name(), t.confidence()));
            }
        }
        return new Mapping.Raw(models, words);
    }

    static NormalForm normalForm(List<Inconsistency> all) {
        var form = new NormalForm(new TreeSet<>(), new TreeSet<>());
        for (Inconsistency i : all) {
            if (i instanceof ModelInconsistency m) {
                form.modelIds().add(m.getModelInstanceUid());
            } else if (i instanceof TextInconsistency t) {
                form.textFindings()
                        .add(t.getSentenceNumber() + " " + t.toFileOutput().getFirst()[2]);
            }
        }
        return form;
    }
}
