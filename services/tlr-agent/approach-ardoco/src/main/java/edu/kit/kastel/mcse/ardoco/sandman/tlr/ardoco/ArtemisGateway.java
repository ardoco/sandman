package edu.kit.kastel.mcse.ardoco.sandman.tlr.ardoco;

import dev.langchain4j.data.embedding.Embedding;
import dev.langchain4j.data.message.AiMessage;
import dev.langchain4j.data.message.ChatMessage;
import dev.langchain4j.data.message.SystemMessage;
import dev.langchain4j.data.message.UserMessage;
import dev.langchain4j.model.chat.ChatModel;
import dev.langchain4j.model.chat.request.ChatRequest;
import dev.langchain4j.model.chat.response.ChatResponse;
import edu.kit.kastel.mcse.ardoco.core.api.entity.ModelEntity;
import edu.kit.kastel.mcse.ardoco.core.api.models.ArchitectureComponentModel;
import edu.kit.kastel.mcse.ardoco.core.api.models.ArchitectureModelWithComponentsAndInterfaces;
import edu.kit.kastel.mcse.ardoco.core.api.models.ModelStates;
import edu.kit.kastel.mcse.ardoco.core.api.models.architecture.ArchitectureComponent;
import edu.kit.kastel.mcse.ardoco.core.api.models.architecture.ArchitectureItem;
import edu.kit.kastel.mcse.ardoco.core.api.stage.connectiongenerator.artemis.ArtemisConnectionStates;
import edu.kit.kastel.mcse.ardoco.core.api.stage.connectiongenerator.artemis.NamedArchitectureEntity;
import edu.kit.kastel.mcse.ardoco.core.api.stage.connectiongenerator.artemis.NamedArchitectureEntityOccurrence;
import edu.kit.kastel.mcse.ardoco.core.common.util.DataRepositoryHelper;
import edu.kit.kastel.mcse.ardoco.core.data.DataRepository;
import edu.kit.kastel.mcse.ardoco.core.pipeline.AbstractExecutionStage;
import edu.kit.kastel.mcse.ardoco.core.pipeline.agent.Informant;
import edu.kit.kastel.mcse.ardoco.core.pipeline.agent.PipelineAgent;
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
import edu.kit.kastel.mcse.ardoco.tlr.models.informants.LlmArchitecturePrompt;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.TreeSet;
import org.eclipse.collections.api.factory.Lists;
import org.eclipse.collections.api.factory.SortedMaps;
import org.eclipse.collections.api.map.sorted.ImmutableSortedMap;

/**
 * The LLM side of the ArTEMiS approaches (T3.8) on the gateway. ArDoCo's {@code LargeLanguageModel} reads provider
 * credentials from the environment, caches on disk, and its embedding call goes to api.openai.com, so the NER, the
 * embedding and the architecture prompts use the gateway client here. Same pattern as the ArTEMiS detector of the
 * inconsistency agent.
 */
final class ArtemisGateway {

    static final ImmutableSortedMap<String, String> NO_CONFIG = SortedMaps.immutable.empty();
    static final int SEED = 42;

    private ArtemisGateway() {}

    /** NAER accepts only lines ending in a sentence terminator. */
    static String terminated(String line) {
        return line.matches("(?s).*[.!?]$") ? line : line + ".";
    }

    /** NER and connection agents of each strategy, on the gateway. */
    static AbstractExecutionStage stage(
            DataRepository repo, List<ArtemisNerStrategy> strategies, LlmClient client, LlmAccess access) {
        List<PipelineAgent> agents = new ArrayList<>();
        for (ArtemisNerStrategy s : strategies) {
            String type = s.getNamedEntityType().name();
            agents.add(new PipelineAgent(List.of(new Ner(repo, s, client, access)), type + "NerAgent", repo) {});
            agents.add(
                    new PipelineAgent(List.of(connection(repo, s, client, access)), type + "ConnectionAgent", repo) {});
        }
        var stage = new AbstractExecutionStage(agents, "ArtemisGatewayStage", repo) {
            @Override
            protected void initializeState() {
                getDataRepository()
                        .addData(
                                ArtemisConnectionStates.ID,
                                ArtemisConnectionStatesImpl.build(strategies.stream()
                                        .map(ArtemisNerStrategy::getTarget)
                                        .toList()));
            }
        };
        stage.applyConfiguration(NO_CONFIG);
        return stage;
    }

    private static Informant connection(
            DataRepository repo, ArtemisNerStrategy strategy, LlmClient client, LlmAccess access) {
        return switch (strategy.getNamedEntityType()) {
            case COMPONENT -> new Connection(repo, strategy, client, access);
            case CLASS -> new ClassArtemisConnectionInformant(repo, strategy);
            case DATAFILE -> new DatafileArtemisConnectionInformant(repo, strategy);
            default -> throw new IllegalArgumentException("unsupported ArTEMiS type " + strategy.getNamedEntityType());
        };
    }

    /** The architecture description of the SAD by the LLM, as a model of component names (ExArch, document only). */
    static PipelineAgent docArchitecture(DataRepository repo, LlmClient client, LlmAccess access) {
        return new PipelineAgent(
                List.of(new DocArchitecture(repo, client, access)), "LlmArchitectureProviderAgent", repo) {};
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
                    .chatModel(new ChatBridge(client, access))
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

    /** Matching as upstream; the embedding stage asks the gateway instead of OpenAI. */
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

    // ponytail: documentation prompt of ArDoCo's LlmArchitectureProviderInformant only, no code prompt, no aggregation.
    // Component names as upstream (strip "Component(s)", camel case, distinct, sorted). Upgrade path: add the code
    // prompt when ExArch on code is wanted.
    private static final class DocArchitecture extends Informant {
        private final LlmClient client;
        private final LlmAccess access;

        DocArchitecture(DataRepository repo, LlmClient client, LlmAccess access) {
            super("LlmArchitectureProviderInformant", repo);
            this.client = client;
            this.access = access;
        }

        @Override
        protected void process() {
            ChatModel chat = new ChatBridge(client, access);
            List<String> templates = LlmArchitecturePrompt.EXTRACT_FROM_ARCHITECTURE.getTemplates();
            List<ChatMessage> messages = new ArrayList<>();
            messages.add(UserMessage.from(
                    templates.getFirst().formatted(DataRepositoryHelper.getInputText(dataRepository))));
            AiMessage answer = chat.chat(messages).aiMessage();
            messages.add(answer);
            for (String next : templates.stream().skip(1).toList()) {
                messages.add(UserMessage.from(next));
                answer = chat.chat(messages).aiMessage();
                messages.add(answer);
            }
            List<String> names = new ArrayList<>();
            for (String line : answer.text().split("\n")) {
                if (line.trim().startsWith("-")) {
                    names.add(line.trim().substring(1).trim());
                }
            }
            List<ArchitectureItem> components = names.stream()
                    .map(n ->
                            n.replace("Components", "").replace("Component", "").trim())
                    .map(DocArchitecture::camelCase)
                    .filter(n -> !n.isBlank())
                    .distinct()
                    .sorted()
                    .<ArchitectureItem>map(n -> new ArchitectureComponent(
                            n, n, new TreeSet<>(), new TreeSet<>(), new TreeSet<>(), "Component"))
                    .toList();
            var model = new ArchitectureComponentModel(new ArchitectureModelWithComponentsAndInterfaces(components));
            var states =
                    dataRepository.getData(ModelStates.ID, ModelStates.class).orElseGet(ModelStates::new);
            states.addModel(model.getMetamodel(), model);
            if (dataRepository.getData(ModelStates.ID, ModelStates.class).isEmpty()) {
                dataRepository.addData(ModelStates.ID, states);
            }
        }

        private static String camelCase(String name) {
            if (!name.contains(" ")) {
                return name;
            }
            StringBuilder b = new StringBuilder();
            for (String part : name.split(" ")) {
                if (!part.isBlank()) {
                    b.append(part.substring(0, 1).toUpperCase());
                    b.append(part.substring(1).toLowerCase());
                }
            }
            return b.toString();
        }
    }

    /** NAER's chat model over the gateway client: virtual key, routes and policy of {@link LlmAccess}. */
    static final class ChatBridge implements ChatModel {
        private final LlmClient client;
        private final String route;

        ChatBridge(LlmClient client, LlmAccess access) {
            this.client = client;
            this.route = access.chatRoute();
        }

        @Override
        public ChatResponse doChat(ChatRequest request) {
            List<LlmClient.Message> messages = request.messages().stream()
                    .map(m -> switch (m) {
                        case SystemMessage s -> new LlmClient.Message("system", s.text());
                        case UserMessage u -> new LlmClient.Message("user", u.singleText());
                        case AiMessage a -> new LlmClient.Message("assistant", a.text());
                        default -> throw new IllegalArgumentException("unsupported message type " + m.type());
                    })
                    .toList();
            return ChatResponse.builder()
                    .aiMessage(
                            AiMessage.from(client.chat(route, messages, 0, SEED).content()))
                    .build();
        }
    }
}
