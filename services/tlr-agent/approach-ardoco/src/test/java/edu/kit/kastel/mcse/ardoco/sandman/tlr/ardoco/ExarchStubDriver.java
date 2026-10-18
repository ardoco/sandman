package edu.kit.kastel.mcse.ardoco.sandman.tlr.ardoco;

import com.sun.net.httpserver.HttpServer;
import edu.kit.kastel.mcse.ardoco.core.api.PreprocessingData;
import edu.kit.kastel.mcse.ardoco.core.api.models.Metamodel;
import edu.kit.kastel.mcse.ardoco.core.api.models.code.CodeCompilationUnit;
import edu.kit.kastel.mcse.ardoco.core.api.output.ArdocoResult;
import edu.kit.kastel.mcse.ardoco.core.common.util.DataRepositoryHelper;
import edu.kit.kastel.mcse.ardoco.core.data.DataRepository;
import edu.kit.kastel.mcse.ardoco.sandman.ardocotext.CorenlpClient;
import edu.kit.kastel.mcse.ardoco.tlr.execution.ExArch;
import edu.kit.kastel.mcse.ardoco.tlr.models.agents.CodeConfiguration;
import edu.kit.kastel.mcse.ardoco.tlr.models.informants.LargeLanguageModel;
import edu.kit.kastel.mcse.ardoco.tlr.models.informants.LlmArchitecturePrompt;
import edu.kit.kastel.mcse.ardoco.tlr.models.informants.LlmArchitectureProviderInformant;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.Set;
import java.util.TreeSet;
import java.util.UUID;
import org.eclipse.collections.api.factory.SortedMaps;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

/**
 * Forked by {@link ExarchPortTest}: ArDoCo reads its LLM settings from the environment only, so the real ExArch
 * informant runs in a JVM with OLLAMA_HOST pointing at this stub of POST /api/chat. The stub answers the second user
 * message (the extraction prompt) with the given answer.
 */
public final class ExarchStubDriver {

    private static volatile String answer = "";

    private ExarchStubDriver() {}

    /** Modes: {@code vectors <port> <vectors.json>} prints one JSON name array per vector; {@code exarch <port> <text> <acm> <names,...>} prints "sentence path" lines. */
    public static void main(String[] args) throws Exception {
        HttpServer server = stub(Integer.parseInt(args[1]));
        try {
            if ("vectors".equals(args[0])) {
                vectors(Path.of(args[2]));
            } else {
                exarch(Path.of(args[2]), Path.of(args[3]), args[4]);
            }
        } finally {
            server.stop(0);
        }
        System.exit(0);
    }

    private static void vectors(Path file) throws Exception {
        JsonNode vectors = JsonMapper.shared().readTree(Files.readString(file));
        List<List<String>> all = new ArrayList<>();
        int i = 0;
        for (JsonNode v : vectors) {
            answer = v.get("answer").asString();
            var repo = new DataRepository();
            DataRepositoryHelper.putInputText(repo, "vector text " + i++ + " " + UUID.randomUUID());
            new LlmArchitectureProviderInformant(
                            repo,
                            LargeLanguageModel.OLLAMA_GENERIC,
                            LlmArchitecturePrompt.EXTRACT_FROM_ARCHITECTURE,
                            null,
                            null,
                            null)
                    .run();
            all.add(
                    DataRepositoryHelper.getModelStatesData(repo)
                            .getModel(Metamodel.ARCHITECTURE_WITH_COMPONENTS)
                            .getContent()
                            .stream()
                            .map(e -> e.getName())
                            .toList());
        }
        System.out.println("RESULT " + JsonMapper.shared().writeValueAsString(all));
    }

    private static void exarch(Path text, Path acm, String names) throws Exception {
        answer = String.join(
                "\n",
                java.util.Arrays.stream(names.split(",")).map(n -> "- " + n).toList());
        var annotated =
                CorenlpClient.convert(CorenlpClient.fromEnv().request(Files.readString(text), false, () -> false));
        Path out = Files.createTempDirectory("exarch-");
        var runner = new ExArch("bench");
        runner.setUp(
                text.toFile(),
                new CodeConfiguration(acm.toFile(), CodeConfiguration.CodeConfigurationType.ACM_FILE),
                SortedMaps.immutable.empty(),
                out.toFile(),
                LargeLanguageModel.OLLAMA_GENERIC,
                LlmArchitecturePrompt.EXTRACT_FROM_ARCHITECTURE,
                null,
                null,
                null);
        DataRepositoryHelper.putPreprocessingData(
                runner.getArdoco().getDataRepository(), new PreprocessingData(annotated));
        var result = new ArdocoResult(runner.runWithoutSaving());
        Set<String> links = new TreeSet<>();
        result.getSadCodeTraceLinks()
                .forEach(tl -> links.add((tl.getFirstEndpoint().getSentence().getSentenceNumber()) + " "
                        + ArdocodeApproach.pathOf((CodeCompilationUnit) tl.getSecondEndpoint())));
        links.forEach(l -> System.out.println("LINK " + l));
        System.out.println("NAMES "
                + JsonMapper.shared()
                        .writeValueAsString(
                                result.getModelState(Metamodel.ARCHITECTURE_WITH_COMPONENTS).getContent().stream()
                                        .map(e -> e.getName())
                                        .sorted()
                                        .toList()));
    }

    private static HttpServer stub(int port) throws Exception {
        var server = HttpServer.create(new InetSocketAddress("127.0.0.1", port), 0);
        server.createContext("/api/chat", ex -> {
            String body = new String(ex.getRequestBody().readAllBytes(), StandardCharsets.UTF_8);
            int users = JsonMapper.shared()
                    .readTree(body)
                    .get("messages")
                    .valueStream()
                    .mapToInt(m -> "user".equals(m.get("role").asString()) ? 1 : 0)
                    .sum();
            String text = users >= 2 ? answer : "The documentation describes some components.";
            var reply = JsonMapper.shared().createObjectNode();
            reply.put("model", "stub");
            reply.put("created_at", "2026-01-01T00:00:00Z");
            reply.putObject("message").put("role", "assistant").put("content", text);
            reply.put("done", true);
            reply.put("done_reason", "stop");
            reply.put("prompt_eval_count", 1);
            reply.put("eval_count", 1);
            byte[] bytes = JsonMapper.shared().writeValueAsBytes(reply);
            ex.getResponseHeaders().add("Content-Type", "application/json");
            ex.sendResponseHeaders(200, bytes.length);
            ex.getResponseBody().write(bytes);
            ex.close();
        });
        server.start();
        return server;
    }
}
