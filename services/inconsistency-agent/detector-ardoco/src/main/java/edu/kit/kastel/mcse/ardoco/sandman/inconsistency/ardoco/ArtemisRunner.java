package edu.kit.kastel.mcse.ardoco.sandman.inconsistency.ardoco;

import com.fasterxml.jackson.datatype.jdk8.Jdk8Module;
import dev.langchain4j.data.embedding.Embedding;
import edu.kit.kastel.mcse.ardoco.core.api.entity.ModelEntity;
import edu.kit.kastel.mcse.ardoco.core.api.models.CodeModel;
import edu.kit.kastel.mcse.ardoco.core.api.models.CodeModelWithCompilationUnits;
import edu.kit.kastel.mcse.ardoco.core.api.models.Metamodel;
import edu.kit.kastel.mcse.ardoco.core.api.models.ModelFormat;
import edu.kit.kastel.mcse.ardoco.core.api.models.ModelStates;
import edu.kit.kastel.mcse.ardoco.core.api.stage.connectiongenerator.artemis.ArtemisConnectionStates;
import edu.kit.kastel.mcse.ardoco.core.api.stage.connectiongenerator.artemis.NamedArchitectureEntity;
import edu.kit.kastel.mcse.ardoco.core.api.stage.connectiongenerator.artemis.NamedArchitectureEntityOccurrence;
import edu.kit.kastel.mcse.ardoco.core.api.stage.inconsistency.ArtemisInconsistencyStates;
import edu.kit.kastel.mcse.ardoco.core.api.stage.inconsistency.Inconsistency;
import edu.kit.kastel.mcse.ardoco.core.api.stage.inconsistency.ModelInconsistency;
import edu.kit.kastel.mcse.ardoco.core.common.IdentifierProvider;
import edu.kit.kastel.mcse.ardoco.core.common.JsonHandling;
import edu.kit.kastel.mcse.ardoco.core.common.util.DataRepositoryHelper;
import edu.kit.kastel.mcse.ardoco.core.data.DataRepository;
import edu.kit.kastel.mcse.ardoco.core.execution.Ardoco;
import edu.kit.kastel.mcse.ardoco.core.pipeline.AbstractExecutionStage;
import edu.kit.kastel.mcse.ardoco.core.pipeline.agent.Informant;
import edu.kit.kastel.mcse.ardoco.core.pipeline.agent.PipelineAgent;
import edu.kit.kastel.mcse.ardoco.id.ArtemisInconsistencyChecker;
import edu.kit.kastel.mcse.ardoco.id.states.ArtemisInconsistencyStatesImpl;
import edu.kit.kastel.mcse.ardoco.id.types.TextEntityAbsentFromModelInconsistency;
import edu.kit.kastel.mcse.ardoco.naer.model.NamedEntity;
import edu.kit.kastel.mcse.ardoco.naer.model.SoftwareArchitectureDocumentation;
import edu.kit.kastel.mcse.ardoco.naer.recognizer.NamedEntityRecognizer;
import edu.kit.kastel.mcse.ardoco.sandman.agent.LlmAccess;
import edu.kit.kastel.mcse.ardoco.sandman.agent.LlmClient;
import edu.kit.kastel.mcse.ardoco.tlr.artemis.informants.ClassArtemisConnectionInformant;
import edu.kit.kastel.mcse.ardoco.tlr.artemis.informants.ComponentArtemisConnectionInformant;
import edu.kit.kastel.mcse.ardoco.tlr.artemis.informants.DatafileArtemisConnectionInformant;
import edu.kit.kastel.mcse.ardoco.tlr.artemis.states.ArtemisConnectionStatesImpl;
import edu.kit.kastel.mcse.ardoco.tlr.artemis.strategies.ArtemisNerStrategy;
import edu.kit.kastel.mcse.ardoco.tlr.artemis.strategies.ClassArtemisNerStrategy;
import edu.kit.kastel.mcse.ardoco.tlr.artemis.strategies.ComponentArtemisNerStrategy;
import edu.kit.kastel.mcse.ardoco.tlr.artemis.strategies.DatafileArtemisNerStrategy;
import edu.kit.kastel.mcse.ardoco.tlr.models.agents.ArchitectureConfiguration;
import edu.kit.kastel.mcse.ardoco.tlr.models.agents.ModelProviderAgent;
import edu.kit.kastel.mcse.ardoco.tlr.text.providers.SimpleTextPreprocessingAgent;
import java.nio.file.Path;
import java.util.ArrayList;
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
    static final String COMPONENT = "component";
    static final String CLASS = "class";
    static final String DATAFILE = "datafile";
    static final List<String> ALL_TARGETS = List.of(COMPONENT, CLASS, DATAFILE);

    private ArtemisRunner() {}

    static ArtemisNerStrategy strategy(String target) {
        return switch (target) {
            case COMPONENT -> new ComponentArtemisNerStrategy();
            case CLASS -> new ClassArtemisNerStrategy();
            case DATAFILE -> new DatafileArtemisNerStrategy();
            default -> throw new IllegalArgumentException("unknown ArTEMiS strategy " + target);
        };
    }

    /** Component only, no code (the original T3.2 behaviour). */
    static Mapping.Raw run(List<String> lines, Path model, ModelFormat format, LlmClient client, LlmAccess access) {
        return run(lines, model, format, null, List.of(COMPONENT), client, access);
    }

    /**
     * The text must have one sentence per line. Raw results of one run (call under {@link IdRunner#LOCK}). The
     * component strategy needs {@code model}, the class and datafile strategies need the ACM file {@code acm}. TEAM
     * results of every strategy become word hits tagged with their target, MEAT results (components only) model hits.
     */
    static Mapping.Raw run(
            List<String> lines,
            Path model,
            ModelFormat format,
            Path acm,
            List<String> targets,
            LlmClient client,
            LlmAccess access) {
        List<ArtemisNerStrategy> strategies =
                targets.stream().map(ArtemisRunner::strategy).toList();
        boolean needsModel = targets.contains(COMPONENT);
        boolean needsCode = targets.contains(CLASS) || targets.contains(DATAFILE);
        if (needsModel && model == null) {
            throw new IllegalArgumentException("the component strategy needs a model");
        }
        if (needsCode && acm == null) {
            throw new IllegalArgumentException("the class and datafile strategies need a code model");
        }
        var ardoco = new Ardoco("sandman-artemis");
        DataRepository repo = ardoco.getDataRepository();
        DataRepositoryHelper.putInputText(
                repo,
                String.join("\n", lines.stream().map(ArtemisRunner::terminated).toList()));
        ardoco.addPipelineStep(SimpleTextPreprocessingAgent.get(NO_CONFIG, repo));
        if (needsCode) {
            readCodeModel(acm, repo);
        }
        if (needsModel) {
            ardoco.addPipelineStep(ModelProviderAgent.getModelProviderAgent(
                    repo,
                    NO_CONFIG,
                    new ArchitectureConfiguration(model.toFile(), format, Metamodel.ARCHITECTURE_WITH_COMPONENTS),
                    null));
        }
        ardoco.addPipelineStep(new GatewayStage(repo, strategies, client, access));
        ardoco.addPipelineStep(ArtemisInconsistencyChecker.get(NO_CONFIG, repo, strategies));
        ardoco.run();
        var states = repo.getData(ArtemisInconsistencyStates.ID, ArtemisInconsistencyStatesImpl.class)
                .orElseThrow();
        List<Mapping.ModelHit> models = new ArrayList<>();
        List<Mapping.WordHit> words = new ArrayList<>();
        for (int i = 0; i < strategies.size(); i++) {
            for (Inconsistency inc :
                    states.getState(strategies.get(i).getTarget()).getInconsistencies()) {
                if (inc instanceof ModelInconsistency m) {
                    String[] row = m.toFileOutput().getFirst();
                    models.add(new Mapping.ModelHit(m.getModelInstanceUid(), row[1], row[2]));
                } else if (inc instanceof TextEntityAbsentFromModelInconsistency t) {
                    words.add(new Mapping.WordHit(t.getSentenceNumber(), t.name(), t.confidence(), targets.get(i)));
                }
            }
        }
        return new Mapping.Raw(models, words);
    }

    /** The ACM read like ArDoCo's ModelProviderInformant does (Jackson, then IdentifierProvider.reset). */
    private static void readCodeModel(Path acm, DataRepository repo) {
        try {
            var mapper = JsonHandling.createObjectMapper();
            mapper.registerModule(new Jdk8Module());
            CodeModel code =
                    new CodeModelWithCompilationUnits(mapper.readValue(acm.toFile(), CodeModel.CodeModelDto.class));
            var states = new ModelStates();
            states.addModel(Metamodel.CODE_WITH_COMPILATION_UNITS, code);
            repo.addData(ModelStates.ID, states);
            IdentifierProvider.reset();
        } catch (java.io.IOException e) {
            throw new java.io.UncheckedIOException(e);
        }
    }

    /** NAER accepts only lines ending in a sentence terminator. */
    static String terminated(String line) {
        return line.matches("(?s).*[.!?]$") ? line : line + ".";
    }

    /** NER and connection agents of the Artemis component strategy, on the gateway. */
    private static final class GatewayStage extends AbstractExecutionStage {

        private final List<ArtemisNerStrategy> strategies;

        GatewayStage(DataRepository repo, List<ArtemisNerStrategy> strategies, LlmClient client, LlmAccess access) {
            super(agents(repo, strategies, client, access), "ArtemisGatewayStage", repo);
            this.strategies = strategies;
            applyConfiguration(NO_CONFIG);
        }

        private static List<PipelineAgent> agents(
                DataRepository repo, List<ArtemisNerStrategy> strategies, LlmClient client, LlmAccess access) {
            List<PipelineAgent> agents = new ArrayList<>();
            for (ArtemisNerStrategy s : strategies) {
                String type = s.getNamedEntityType().name();
                agents.add(new PipelineAgent(List.of(new Ner(repo, s, client, access)), type + "NerAgent", repo) {});
                Informant connection =
                        switch (s.getNamedEntityType()) {
                            case COMPONENT -> new Connection(repo, s, client, access);
                            case CLASS -> new ClassArtemisConnectionInformant(repo, s);
                            case DATAFILE -> new DatafileArtemisConnectionInformant(repo, s);
                            default -> throw new IllegalArgumentException("unsupported type " + s.getNamedEntityType());
                        };
                agents.add(new PipelineAgent(List.of(connection), type + "ConnectionAgent", repo) {});
            }
            return agents;
        }

        @Override
        protected void initializeState() {
            getDataRepository()
                    .addData(
                            ArtemisConnectionStates.ID,
                            ArtemisConnectionStatesImpl.build(strategies.stream()
                                    .map(ArtemisNerStrategy::getTarget)
                                    .toList()));
        }
    }

    private static final class Ner extends Informant {
        private final ArtemisNerStrategy strategy;
        private final LlmClient client;
        private final LlmAccess access;

        Ner(DataRepository repo, ArtemisNerStrategy strategy, LlmClient client, LlmAccess access) {
            super(strategy.getNamedEntityType().name() + "NerInformant", repo);
            this.strategy = strategy;
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
                    .prompt(strategy.createPrompt(dataRepository))
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
            states.getState(strategy.getTarget()).addNamedEntities(Lists.mutable.ofAll(result));
        }
    }

    /** Matching stages as upstream; the embedding stage asks the gateway instead of OpenAI. */
    private static final class Connection extends ComponentArtemisConnectionInformant {
        private final LlmClient client;
        private final String route;
        private final Map<String, Embedding> cache = new HashMap<>();

        Connection(DataRepository repo, ArtemisNerStrategy strategy, LlmClient client, LlmAccess access) {
            super(repo, strategy);
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
