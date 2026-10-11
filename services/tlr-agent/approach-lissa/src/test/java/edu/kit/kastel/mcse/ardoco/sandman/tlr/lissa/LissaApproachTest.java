package edu.kit.kastel.mcse.ardoco.sandman.tlr.lissa;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import edu.kit.kastel.mcse.ardoco.sandman.agent.ArtifactStore;
import edu.kit.kastel.mcse.ardoco.sandman.agent.LlmAccess;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.ApproachUnavailableException;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.CandidateLink;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.ScopeMode;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.TlrContext;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.TlrInput;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

class LissaApproachTest {

    private static final Logger LOG = LoggerFactory.getLogger(LissaApproachTest.class);

    @TempDir
    Path root;

    ArtifactStore store;
    final List<TlrInput.Version> versions = new ArrayList<>();
    final List<TlrInput.Element> targets = new ArrayList<>();

    @BeforeEach
    void setUp() {
        store = new ArtifactStore(root);
    }

    private TlrInput.Element requirement(String id, String text) {
        return new TlrInput.Element(id, "vr", id, id, "Requirement", "requirement", id, text, 0, "requirements");
    }

    private void code(String name, String content, String label, String kind) throws IOException {
        var stored = store.put("proj", content.getBytes(StandardCharsets.UTF_8));
        String version = "v-" + name;
        versions.add(new TlrInput.Version(version, "a-" + name, name, "code", null, stored.uri()));
        targets.add(new TlrInput.Element("e-" + name, version, name, name, label, kind, name, null, null, "code"));
    }

    private TlrInput input(List<TlrInput.Element> sources) {
        return new TlrInput("p", versions, sources, targets);
    }

    private List<TlrInput.Element> threeRequirements() {
        return List.of(requirement("r3", "gamma"), requirement("r1", "alpha"), requirement("r2", "beta"));
    }

    private void fourFiles() throws IOException {
        code("D.java", "delta\r\nline", "CodeUnit", "file");
        code("B.java", "beta", "CodeUnit", "file");
        code("A.java", "alpha", "CodeUnit", "file");
        code("C.java", "gamma", "CodeUnit", "file");
    }

    private static float[] vector(String text) {
        return switch (text.charAt(0)) {
            case 'a' -> new float[] {1f, 0f, 0f};
            case 'b' -> new float[] {0f, 1f, 0f};
            case 'g' -> new float[] {0f, 0f, 1f};
            default -> new float[] {1f, 1f, 1f};
        };
    }

    private FakeGateway gateway() {
        FakeGateway gateway = new FakeGateway();
        gateway.embedAnswer = body -> vector(body.get("input").asString());
        gateway.chatAnswer = body -> {
            String prompt = body.get("messages").get(0).get("content").asString();
            String req = prompt.split("requirement: '''", 2)[1].split("'''", 2)[0];
            String code = prompt.split("source code: '''", 2)[1].split("'''", 2)[0];
            return req.equals(code) ? "Yes" : "no";
        };
        return gateway;
    }

    private TlrContext ctx(FakeGateway gateway, String paramsJson) {
        LlmAccess access = new LlmAccess(
                "http://127.0.0.1",
                "key",
                Map.of(
                        "chat-default", "gpt-x",
                        "embed-default", "emb-x"),
                false);
        JsonNode params = JsonMapper.shared().readTree(paramsJson);
        return new TlrContext(access, store, () -> false, LOG, params, gateway.client());
    }

    @Test
    void linksAreSortedAndOnlyAcceptedPairsRemain() throws IOException {
        fourFiles();
        try (FakeGateway gateway = gateway()) {
            List<CandidateLink> links =
                    new LissaApproach().recover(input(threeRequirements()), ctx(gateway, "{\"retrieval\":{\"k\":2}}"));
            assertEquals(
                    List.of(
                            new CandidateLink("r1", "e-A.java", "implements", 1.0, null),
                            new CandidateLink("r2", "e-B.java", "implements", 1.0, null),
                            new CandidateLink("r3", "e-C.java", "implements", 1.0, null)),
                    links);
            assertEquals(3 * 2, gateway.chatBodies.size());
            assertEquals(3 + 4, gateway.embedBodies.size());
        }
    }

    @Test
    void codeContentIsReadFromStoreWithLfLineEndings() throws IOException {
        fourFiles();
        try (FakeGateway gateway = gateway()) {
            new LissaApproach().recover(input(threeRequirements()), ctx(gateway, "{}"));
            assertTrue(gateway.embedBodies.stream()
                    .anyMatch(b -> "delta\nline".equals(b.get("input").asString())));
        }
    }

    @Test
    void skipsOtherLabelsAndCodeKinds() throws IOException {
        code("A.java", "alpha", "CodeUnit", "file");
        code("Sub.java", "alpha", "CodeUnit", "method");
        code("T.java", "alpha", "TextUnit", "file");
        try (FakeGateway gateway = gateway()) {
            List<CandidateLink> links =
                    new LissaApproach().recover(input(List.of(requirement("r1", "alpha"))), ctx(gateway, "{}"));
            assertEquals(1, links.size());
            assertEquals(2, gateway.embedBodies.size());
        }
    }

    @Test
    void noSourcesOrTargetsGiveNoCallsAndNoLinks() throws IOException {
        try (FakeGateway gateway = gateway()) {
            assertEquals(
                    List.of(),
                    new LissaApproach().recover(input(List.of(requirement("r1", "alpha"))), ctx(gateway, "{}")));
            assertEquals(0, gateway.embedBodies.size());
        }
    }

    @Test
    void environmentAndPromptIdsFollowClassifierAndK() {
        try (FakeGateway gateway = gateway()) {
            var approach = new LissaApproach();
            String simple = approach.environment(ctx(gateway, "{}"));
            assertEquals(simple, approach.environment(ctx(gateway, "{\"classifier\":\"simple\"}")));
            JsonNode env = JsonMapper.shared().readTree(simple);
            assertEquals("gpt-x", env.get("chatModel").asString());
            assertEquals("emb-x", env.get("embedModel").asString());
            assertEquals(20, env.get("k").asInt());
            assertEquals(133742243, env.get("seed").asInt());
            assertEquals(LissaApproach.LISSA_COMMIT, env.get("lissaCommit").asString());
            assertNotEquals(simple, approach.environment(ctx(gateway, "{\"classifier\":\"reasoning\"}")));
            assertNotEquals(simple, approach.environment(ctx(gateway, "{\"retrieval\":{\"k\":3}}")));
            List<String> ids = approach.promptIds(ctx(gateway, "{}"));
            assertEquals(1, ids.size());
            assertTrue(ids.get(0).matches("lissa-simple@[0-9a-f]{8}"));
            assertTrue(approach.promptIds(ctx(gateway, "{\"classifier\":\"reasoning\"}"))
                    .get(0)
                    .startsWith("lissa-reasoning@"));
        }
    }

    @Test
    void declaresWholeProjectLlmApproachForRequirementsToCode() {
        var approach = new LissaApproach();
        assertEquals("1.1.0+s1", approach.version());
        assertTrue(approach.usesLlm());
        assertEquals(ScopeMode.WHOLE_PROJECT, approach.scopeMode());
        assertEquals(
                java.util.Set.of(new edu.kit.kastel.mcse.ardoco.sandman.tlr.KindPair("requirements", "code")),
                approach.supports());
    }

    @Test
    void unknownClassifierIsRejected() {
        try (FakeGateway gateway = gateway()) {
            assertThrows(
                    IllegalArgumentException.class,
                    () -> new LissaApproach().environment(ctx(gateway, "{\"classifier\":\"other\"}")));
        }
    }

    @Test
    void lissaCommitConstantEqualsFile() throws IOException {
        Path file = Path.of("LISSA_COMMIT");
        assertEquals(Files.readString(file).strip(), LissaApproach.LISSA_COMMIT);
    }

    @Test
    void overLimitInputIsSkippedWithWarning() throws IOException {
        fourFiles();
        try (FakeGateway gateway = gateway()) {
            gateway.embedStatus = 400;
            gateway.embedErrorBody = "{\"error\":{\"code\":\"context_length_exceeded\"}}";
            gateway.embedFails = body -> "delta\nline".equals(body.get("input").asString());
            List<CandidateLink> links = new LissaApproach().recover(input(threeRequirements()), ctx(gateway, "{}"));
            assertEquals(3, links.size());
            assertTrue(links.stream().noneMatch(l -> l.targetId().equals("e-D.java")));
        }
    }

    @Test
    void unavailableGatewayBecomesApproachUnavailable() throws IOException {
        fourFiles();
        try (FakeGateway gateway = gateway()) {
            gateway.chatStatus = 503;
            assertThrows(
                    ApproachUnavailableException.class,
                    () -> new LissaApproach().recover(input(threeRequirements()), ctx(gateway, "{}")));
        }
    }

    @Test
    void secondRunAfterAChangeEqualsFreshBatchRun() throws IOException {
        fourFiles();
        try (FakeGateway gateway = gateway()) {
            var approach = new LissaApproach();
            approach.recover(input(threeRequirements()), ctx(gateway, "{}"));
            versions.removeIf(v -> v.id().equals("v-C.java"));
            targets.removeIf(t -> t.id().equals("e-C.java"));
            code("C.java", "beta", "CodeUnit", "file");
            List<CandidateLink> incremental = approach.recover(input(threeRequirements()), ctx(gateway, "{}"));
            List<CandidateLink> batch = new LissaApproach().recover(input(threeRequirements()), ctx(gateway, "{}"));
            assertEquals(batch, incremental);
        }
    }
}
