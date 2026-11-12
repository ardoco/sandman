package edu.kit.kastel.mcse.ardoco.sandman.tlr.ardoco;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.sun.net.httpserver.HttpServer;
import edu.kit.kastel.mcse.ardoco.sandman.agent.ArtifactStore;
import edu.kit.kastel.mcse.ardoco.sandman.agent.LlmAccess;
import edu.kit.kastel.mcse.ardoco.sandman.agent.LlmClient;
import edu.kit.kastel.mcse.ardoco.sandman.agent.a2a.A2aClient.TaskSnapshot;
import edu.kit.kastel.mcse.ardoco.sandman.ardocotext.SentenceRendering;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.ApproachUnavailableException;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.CandidateLink;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.KindPair;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.ScopeMode;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.TlrContext;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.TlrInput;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.TraceLinkApproach;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.stream.Stream;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.slf4j.LoggerFactory;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

/** The five ArTEMiS approaches against a stub gateway and a stub code model plugin: no credential, no network. */
class ArtemisApproachTest {

    private static final String SENTENCE = "The Greeter class greets the users of the system.";
    private static final String DOCKER_SENTENCE = "The Dockerfile builds the image of the system.";
    private static final String JAVA_PATH = "src/main/java/com/acme/Greeter.java";

    private HttpServer server;
    private final List<String> paths = new CopyOnWriteArrayList<>();
    private volatile String nerAnswer = "[]";
    private volatile int chatStatus = 200;

    @BeforeEach
    void startStub() throws Exception {
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/", ex -> {
            String body = new String(ex.getRequestBody().readAllBytes(), StandardCharsets.UTF_8);
            paths.add(ex.getRequestURI().getPath());
            if (chatStatus != 200 && !ex.getRequestURI().getPath().endsWith("/embeddings")) {
                ex.sendResponseHeaders(chatStatus, -1);
                ex.close();
                return;
            }
            String json;
            if (ex.getRequestURI().getPath().endsWith("/embeddings")) {
                int dim = Math.floorMod(body.hashCode(), 64);
                StringBuilder v = new StringBuilder();
                for (int i = 0; i < 64; i++) {
                    v.append(i == 0 ? "" : ",").append(i == dim ? 1 : 0);
                }
                json = "{\"data\":[{\"embedding\":[" + v + "]}],\"usage\":{\"prompt_tokens\":1}}";
            } else {
                json = "{\"choices\":[{\"message\":{\"content\":"
                        + JsonMapper.shared().writeValueAsString(body.contains("occurrences") ? nerAnswer : "- Greeter")
                        + "},\"finish_reason\":\"stop\"}],\"model\":\"stub\",\"usage\":{\"prompt_tokens\":2,\"completion_tokens\":3}}";
            }
            byte[] out = json.getBytes(StandardCharsets.UTF_8);
            ex.getResponseHeaders().add("Content-Type", "application/json");
            ex.sendResponseHeaders(200, out.length);
            ex.getResponseBody().write(out);
            ex.close();
        });
        server.start();
    }

    @AfterEach
    void stopStub() {
        server.stop(0);
    }

    private LlmAccess access() {
        return new LlmAccess(
                "http://127.0.0.1:" + server.getAddress().getPort(),
                "vk-test",
                Map.of("chat-default", "gpt-chat", "embed-default", "emb-model"),
                false);
    }

    private TlrContext ctx(Path tmp) {
        var access = access();
        return new TlrContext(
                access,
                new ArtifactStore(tmp.resolve("store")),
                () -> false,
                LoggerFactory.getLogger(ArtemisApproachTest.class),
                JsonMapper.shared().createObjectNode(),
                new LlmClient(access, "t"),
                s -> {});
    }

    private static CodeModelClient plugin() throws Exception {
        var acm = (tools.jackson.databind.node.ObjectNode) JsonMapper.shared()
                .readTree(ArtemisApproachTest.class.getResourceAsStream("/fixtures/code-model-with-code-files.acm"));
        var repository = (tools.jackson.databind.node.ObjectNode) acm.at("/codeItemRepository/repository");
        ((tools.jackson.databind.node.ArrayNode) repository.get("acm1").get("content")).add("acm9");
        repository.set("acm9", JsonMapper.shared().readTree("""
                {"type":"ClassUnit","id":"acm9","name":"Greeter","comment":null,"compilationUnitId":"acm1",
                 "parentDatatypeId":null,"startLine":1,"endLine":3,"extendedDataTypesIds":[],
                 "implementedDataTypesIds":[],"datatypeReferencesIds":[],"content":[]}"""));
        var done = new TaskSnapshot(
                "t1",
                "TASK_STATE_COMPLETED",
                JsonMapper.shared().createObjectNode().set("acm", acm),
                null);
        return new CodeModelClient("http://plugin", Duration.ofSeconds(5), Duration.ZERO, new CodeModelClient.Calls() {
            @Override
            public String identity(String url) {
                return "code-extractor-ardoco@test";
            }

            @Override
            public TaskSnapshot submit(String url, JsonNode input, String messageId) {
                return done;
            }

            @Override
            public TaskSnapshot get(String url, String taskId) {
                return done;
            }

            @Override
            public TaskSnapshot cancel(String url, String taskId) {
                return done;
            }
        });
    }

    private static TlrInput.Element sentence(int index, String text) {
        return new TlrInput.Element(
                "el_s" + index,
                "ver_doc",
                "art_doc#sec:/" + SentenceRendering.hash(text),
                "sec:/" + SentenceRendering.hash(text),
                "TextUnit",
                "sentence",
                text,
                text,
                index,
                "architecture-doc");
    }

    private static TlrInput.Element file(String id, String path, String label) {
        return new TlrInput.Element(id, "ver_code", "art_code#" + path, path, label, "file", path, null, null, "code");
    }

    private TlrInput input(Path tmp, boolean withModel) throws Exception {
        var store = new ArtifactStore(tmp.resolve("store"));
        var doc = new TlrInput.Version(
                "ver_doc",
                "art_doc",
                "sad.md",
                "architecture-doc",
                "markdown",
                store.put("mediastore", "d".getBytes()).uri());
        var code = new TlrInput.Version(
                "ver_code",
                "art_code",
                JAVA_PATH,
                "code",
                null,
                store.put("mediastore", "c".getBytes()).uri());
        var versions = new java.util.ArrayList<>(List.of(doc, code));
        if (withModel) {
            versions.add(new TlrInput.Version(
                    "ver_model",
                    "art_model",
                    "m.components",
                    "architecture-model",
                    "component-listing",
                    store.put("mediastore", "Greeter::cmp1\n".getBytes()).uri()));
        }
        var targets = new java.util.ArrayList<>(
                List.of(file("el_java", JAVA_PATH, "CodeUnit"), file("el_docker", "Dockerfile", "BuildFile")));
        if (withModel) {
            targets.add(new TlrInput.Element(
                    "el_cmp",
                    "ver_model",
                    "art_model#cmp1",
                    "cmp1",
                    "Component",
                    "component",
                    "Greeter",
                    null,
                    null,
                    "architecture-model"));
        }
        return new TlrInput(
                "mediastore", versions, List.of(sentence(0, SENTENCE), sentence(1, DOCKER_SENTENCE)), targets);
    }

    private static String ner(String name, String type, String line) {
        return "[{\"name\":\"%s\",\"type\":\"%s\",\"alternativeNames\":[],\"occurrences\":[\"%s\"]}]"
                .formatted(name, type, line.endsWith(".") ? line : line);
    }

    @Test
    void declarationsFollowTheSpec() {
        Map<String, TraceLinkApproach> all = Map.of(
                "artemis", new ArtemisApproach(),
                "artemis-class", new ArtemisClassApproach(),
                "artemis-datafile", new ArtemisDatafileApproach(),
                "artemis-transarc", new ArtemisTransarcApproach(),
                "artemis-exarch", new ArtemisExarchApproach());
        all.forEach((id, a) -> {
            assertEquals(id, a.id());
            assertEquals("2.2.0-SNAPSHOT+21ef682.a1", a.version());
            assertTrue(a.usesLlm());
            assertEquals(ScopeMode.WHOLE_PROJECT, a.scopeMode());
            assertEquals(Set.of(), a.paramKeys());
        });
        assertEquals(
                Set.of(new KindPair("architecture-doc", "architecture-model")),
                all.get("artemis").supports());
        assertEquals(
                Set.of(new KindPair("architecture-doc", "code")),
                all.get("artemis-class").supports());
        assertEquals(
                Set.of(new KindPair("architecture-doc", "code")),
                all.get("artemis-datafile").supports());
        assertEquals(
                Set.of(new KindPair("architecture-doc", "code")),
                all.get("artemis-exarch").supports());
        assertEquals(
                Set.of(new KindPair("architecture-doc", "code"), new KindPair("architecture-model", "code")),
                all.get("artemis-transarc").supports());
        assertTrue(Stream.of(TraceLinkApproach.class)
                .flatMap(c -> java.util.ServiceLoader.load(c).stream())
                .map(p -> p.get().id())
                .toList()
                .containsAll(all.keySet()));
    }

    @Test
    void classLinksUseTheDocumentsTypeAndTargetEvidence(@TempDir Path tmp) throws Exception {
        nerAnswer = ner("Greeter", "CLASS", SENTENCE);
        List<CandidateLink> links = new ArtemisClassApproach(plugin()).recover(input(tmp, false), ctx(tmp));
        assertEquals(List.of(new CandidateLink("el_s0", "el_java", "documents", 1.0, "target=class")), links);
        assertTrue(paths.stream().anyMatch(p -> p.endsWith("/chat/completions")));
    }

    @Test
    void datafileLinksUseTheDocumentsTypeAndTargetEvidence(@TempDir Path tmp) throws Exception {
        nerAnswer = ner("Greeter.java", "DATAFILE", SENTENCE);
        List<CandidateLink> links = new ArtemisDatafileApproach(plugin()).recover(input(tmp, false), ctx(tmp));
        assertEquals(List.of(new CandidateLink("el_s0", "el_java", "documents", 1.0, "target=datafile")), links);
    }

    @Test
    void componentLinksTargetTheModelElement(@TempDir Path tmp) throws Exception {
        nerAnswer = ner("Greeter", "COMPONENT", SENTENCE);
        List<CandidateLink> links = new ArtemisApproach().recover(input(tmp, true), ctx(tmp));
        assertEquals(List.of(new CandidateLink("el_s0", "el_cmp", "documents", 1.0, "target=component")), links);
    }

    @Test
    void transarcCombinerUsesTheArtemisLinks(@TempDir Path tmp) throws Exception {
        nerAnswer = ner("Greeter", "COMPONENT", SENTENCE);
        List<CandidateLink> links = new ArtemisTransarcApproach(plugin()).recover(input(tmp, true), ctx(tmp));
        assertEquals(
                List.of(
                        new CandidateLink("el_s0", "el_java", "documents", 1.0, "target=component"),
                        new CandidateLink("el_cmp", "el_java", "implements", 1.0, null)),
                links);
    }

    @Test
    void exarchLinksTheSentenceToTheFileThroughTheDocumentArchitecture(@TempDir Path tmp) throws Exception {
        nerAnswer = ner("Greeter", "COMPONENT", SENTENCE);
        List<CandidateLink> links = new ArtemisExarchApproach(plugin()).recover(input(tmp, false), ctx(tmp));
        assertEquals(List.of(new CandidateLink("el_s0", "el_java", "documents", 1.0, "target=component")), links);
    }

    @Test
    void withoutAnLlmRouteTheApproachIsUnavailable(@TempDir Path tmp) throws Exception {
        chatStatus = 503;
        nerAnswer = ner("Greeter", "CLASS", SENTENCE);
        assertThrows(
                ApproachUnavailableException.class,
                () -> new ArtemisClassApproach(plugin()).recover(input(tmp, false), ctx(tmp)));
    }

    @Test
    void noSwattrStageIsPartOfAnyPipeline() throws Exception {
        String base = new String(
                ArtemisApproachTest.class
                        .getResourceAsStream("/edu/kit/kastel/mcse/ardoco/sandman/tlr/ardoco/ArtemisApproachBase.class")
                        .readAllBytes(),
                StandardCharsets.ISO_8859_1);
        assertFalse(base.contains("SwattrConnection"), "SWATTR would hide the ArTEMiS links in TraceLinkCombiner");
        assertFalse(base.contains("ConnectionStates"));
        assertFalse(Files.exists(Path.of(".cache-llm")));
    }
}
