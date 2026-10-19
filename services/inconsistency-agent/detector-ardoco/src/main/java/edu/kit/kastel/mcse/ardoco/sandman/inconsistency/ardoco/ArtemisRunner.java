package edu.kit.kastel.mcse.ardoco.sandman.inconsistency.ardoco;

import dev.langchain4j.data.embedding.Embedding;
import edu.kit.kastel.mcse.ardoco.core.api.entity.ModelEntity;
import edu.kit.kastel.mcse.ardoco.core.api.models.Metamodel;
import edu.kit.kastel.mcse.ardoco.core.api.models.ModelFormat;
import edu.kit.kastel.mcse.ardoco.core.api.stage.connectiongenerator.artemis.ArtemisConnectionStates;
import edu.kit.kastel.mcse.ardoco.core.api.stage.connectiongenerator.artemis.NamedArchitectureEntity;
import edu.kit.kastel.mcse.ardoco.core.api.stage.connectiongenerator.artemis.NamedArchitectureEntityOccurrence;
import edu.kit.kastel.mcse.ardoco.core.api.stage.inconsistency.ArtemisInconsistencyStates;
import edu.kit.kastel.mcse.ardoco.core.api.stage.inconsistency.Inconsistency;
import edu.kit.kastel.mcse.ardoco.core.common.util.DataRepositoryHelper;
import edu.kit.kastel.mcse.ardoco.core.data.DataRepository;
import edu.kit.kastel.mcse.ardoco.core.execution.Ardoco;
import edu.kit.kastel.mcse.ardoco.core.pipeline.AbstractExecutionStage;
import edu.kit.kastel.mcse.ardoco.core.pipeline.agent.Informant;
import edu.kit.kastel.mcse.ardoco.core.pipeline.agent.PipelineAgent;
import edu.kit.kastel.mcse.ardoco.id.ArtemisInconsistencyChecker;
import edu.kit.kastel.mcse.ardoco.id.states.ArtemisInconsistencyStatesImpl;
import edu.kit.kastel.mcse.ardoco.naer.model.NamedEntity;
import edu.kit.kastel.mcse.ardoco.naer.model.SoftwareArchitectureDocumentation;
import edu.kit.kastel.mcse.ardoco.naer.recognizer.NamedEntityRecognizer;
import edu.kit.kastel.mcse.ardoco.sandman.agent.LlmAccess;
import edu.kit.kastel.mcse.ardoco.sandman.agent.LlmClient;
import edu.kit.kastel.mcse.ardoco.tlr.artemis.ArtemisPostprocessing;
import edu.kit.kastel.mcse.ardoco.tlr.artemis.ArtemisPreprocessing;
import edu.kit.kastel.mcse.ardoco.tlr.artemis.informants.ComponentArtemisConnectionInformant;
import edu.kit.kastel.mcse.ardoco.tlr.artemis.states.ArtemisConnectionStatesImpl;
import edu.kit.kastel.mcse.ardoco.tlr.artemis.strategies.ArtemisNerStrategy;
import edu.kit.kastel.mcse.ardoco.tlr.artemis.strategies.ComponentArtemisNerStrategy;
import edu.kit.kastel.mcse.ardoco.tlr.models.agents.ArchitectureConfiguration;
import java.nio.file.Path;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.TreeSet;
import org.eclipse.collections.api.factory.Lists;
import org.eclipse.collections.api.factory.SortedMaps;
import org.eclipse.collections.api.map.sorted.ImmutableSortedMap;

/**
 * Artemis inconsistency pipeline (T3.2) from public classes. ArDoCo's {@code LargeLanguageModel} reads credentials from
 * the environment and caches on disk, and its embedding call goes to api.openai.com. Here NER and embeddings use the
 * gateway client instead.
 */
final class ArtemisRunner {

    private static final ImmutableSortedMap<String, String> NO_CONFIG = SortedMaps.immutable.empty();
    private static final ArtemisNerStrategy STRATEGY = new ComponentArtemisNerStrategy();

    private ArtemisRunner() {}

    /** The text must have one sentence per line. Raw ArDoCo inconsistencies of one run (call under {@link IdRunner#LOCK}). */
    static List<Inconsistency> run(
            List<String> lines, Path model, ModelFormat format, LlmClient client, LlmAccess access) {
        var ardoco = new Ardoco("sandman-artemis");
        DataRepository repo = ardoco.getDataRepository();
        DataRepositoryHelper.putInputText(
                repo,
                String.join("\n", lines.stream().map(ArtemisRunner::terminated).toList()));
        ardoco.addPipelineStep(ArtemisPreprocessing.get(
                NO_CONFIG,
                repo,
                new ArchitectureConfiguration(model.toFile(), format, Metamodel.ARCHITECTURE_WITH_COMPONENTS),
                null));
        ardoco.addPipelineStep(new GatewayStage(repo, client, access));
        ardoco.addPipelineStep(ArtemisPostprocessing.get(NO_CONFIG, repo));
        ardoco.addPipelineStep(ArtemisInconsistencyChecker.get(NO_CONFIG, repo, List.of(STRATEGY)));
        ardoco.run();
        var states = repo.getData(ArtemisInconsistencyStates.ID, ArtemisInconsistencyStatesImpl.class)
                .orElseThrow();
        return List.copyOf(
                states.getState(STRATEGY.getTarget()).getInconsistencies().castToList());
    }

    /** NAER accepts only lines ending in a sentence terminator. */
    static String terminated(String line) {
        return line.matches("(?s).*[.!?]$") ? line : line + ".";
    }

    /** NER and connection agents of the Artemis component strategy, on the gateway. */
    private static final class GatewayStage extends AbstractExecutionStage {

        GatewayStage(DataRepository repo, LlmClient client, LlmAccess access) {
            super(
                    List.of(
                            new PipelineAgent(List.of(new Ner(repo, client, access)), "ComponentNerAgent", repo) {},
                            new PipelineAgent(
                                    List.of(new Connection(repo, client, access)),
                                    "ComponentConnectionAgent",
                                    repo) {}),
                    "ArtemisGatewayStage",
                    repo);
            applyConfiguration(NO_CONFIG);
        }

        @Override
        protected void initializeState() {
            getDataRepository()
                    .addData(
                            ArtemisConnectionStates.ID,
                            ArtemisConnectionStatesImpl.build(List.of(STRATEGY.getTarget())));
        }
    }

    private static final class Ner extends Informant {
        private final LlmClient client;
        private final LlmAccess access;

        Ner(DataRepository repo, LlmClient client, LlmAccess access) {
            super("ComponentNerInformant", repo);
            this.client = client;
            this.access = access;
        }

        @Override
        protected void process() {
            var states = dataRepository
                    .getData(ArtemisConnectionStates.ID, ArtemisConnectionStatesImpl.class)
                    .orElseThrow();
            var sad = new SoftwareArchitectureDocumentation(
                    DataRepositoryHelper.getSimpleText(dataRepository).getText());
            var recognizer = new NamedEntityRecognizer.Builder()
                    .chatModel(new ArtemisChatModel(client, access))
                    .prompt(STRATEGY.createPrompt(dataRepository))
                    .build();
            var entities = Lists.mutable.ofAll(recognizer.recognize(sad));
            entities.sortThisBy(NamedEntity::getName);
            var result = new TreeSet<NamedArchitectureEntity>();
            for (NamedEntity e : entities) {
                var occurrences = Lists.mutable.<NamedArchitectureEntityOccurrence>empty();
                for (int line : e.getOccurrenceLines()) {
                    occurrences.add(new NamedArchitectureEntityOccurrence(e.getName(), line));
                }
                result.add(
                        new NamedArchitectureEntity(e.getName(), new TreeSet<>(e.getAlternativeNames()), occurrences));
            }
            states.getState(STRATEGY.getTarget()).addNamedEntities(Lists.mutable.ofAll(result));
        }
    }

    /** Matching stages as upstream; the embedding stage asks the gateway instead of OpenAI. */
    private static final class Connection extends ComponentArtemisConnectionInformant {
        private final LlmClient client;
        private final String route;
        private final Map<String, Embedding> cache = new HashMap<>();

        Connection(DataRepository repo, LlmClient client, LlmAccess access) {
            super(repo, STRATEGY);
            this.client = client;
            this.route = access.embedRoute();
        }

        @Override
        protected List<Embedding> getModelEndpointEmbeddings(ModelEntity endpoint) {
            return List.of(embed(endpoint.getName()));
        }

        @Override
        protected List<Embedding> getNamedArchitectureEntityEmbeddings(NamedArchitectureEntity entity) {
            var names = new TreeSet<>(entity.getAlternativeNames());
            names.add(entity.getName());
            return names.stream().map(this::embed).toList();
        }

        private Embedding embed(String name) {
            return cache.computeIfAbsent(name, n -> Embedding.from(client.embed(route, n)));
        }
    }
}
