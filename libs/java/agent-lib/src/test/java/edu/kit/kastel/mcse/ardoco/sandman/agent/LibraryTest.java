package edu.kit.kastel.mcse.ardoco.sandman.agent;

import static org.junit.jupiter.api.Assertions.assertArrayEquals;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import tools.jackson.databind.json.JsonMapper;

class LibraryTest {

    static final Contracts CONTRACTS = new Contracts(Settings.defaultContractsDir());

    @Test
    void settingsReadFileSecretsAndReportMissingNames(@TempDir Path tmp) throws Exception {
        Files.writeString(tmp.resolve("pw"), "s3cret\n");
        Files.writeString(tmp.resolve("verify.json"), "{\"orchestrator\": \"ab\"}");
        Settings s = Settings.fromEnv(Map.of(
                "SANDMAN_SERVICE_NAME", "tlr-agent",
                "SANDMAN_KG_PASSWORD_FILE", tmp.resolve("pw").toString(),
                "SANDMAN_SERVICE_VERIFY_FILE", tmp.resolve("verify.json").toString()));
        assertEquals("s3cret", s.kgPassword());
        assertEquals(Map.of("orchestrator", "ab"), s.serviceVerify());
        var e = assertThrows(Settings.ConfigException.class, () -> s.require("kgUri", "natsUrl", "kgPassword"));
        assertEquals("tlr-agent: missing configuration SANDMAN_KG_URI, SANDMAN_NATS_URL", e.getMessage());
        assertThrows(IllegalArgumentException.class, () -> s.require("nope"));
        assertThrows(Settings.ConfigException.class, () -> Settings.fromEnv(Map.of()));
        assertThrows(
                Settings.ConfigException.class,
                () -> Settings.fromEnv(Map.of("SANDMAN_SERVICE_NAME", "x", "SANDMAN_LLM_KEY_FILE", "/does/not/exist")));
    }

    @Test
    void llmModeReadsEnvAndRejectsUnknown() {
        assertEquals("live", Settings.llmMode(Map.of()));
        assertEquals("live", Settings.llmMode(Map.of("SANDMAN_LLM_MODE", "")));
        assertEquals("record", Settings.llmMode(Map.of("SANDMAN_LLM_MODE", "record")));
        assertEquals("replay", Settings.llmMode(Map.of("SANDMAN_LLM_MODE", "replay")));
        var e = assertThrows(
                Settings.ConfigException.class, () -> Settings.llmMode(Map.of("SANDMAN_LLM_MODE", "bogus")));
        assertTrue(e.getMessage().contains("SANDMAN_LLM_MODE"));
    }

    @Test
    void fingerprintSortsByUtf8BytesNotUtf16() {
        String bmp = "Ａ"; // UTF-8 EF BC A1; UTF-16 FF21
        String astral = "😀"; // U+1F600: UTF-8 F0 9F 98 80; UTF-16 D83D DE00
        assertTrue(astral.compareTo(bmp) < 0); // String.compareTo would put the emoji first
        String expected = Ids.sha256Hex(String.join(Ids.SEP, "t", "d", bmp, astral));
        assertEquals(expected, Ids.fingerprint("t", "d", List.of(astral, bmp)));
        assertEquals(expected, Ids.fingerprint("t", "d", List.of(bmp, astral)));
        assertTrue(Ids.newRunId().matches("run_[0-9a-f]{32}"));
        assertTrue(Ids.newWorkflowId().matches("wf_[0-9a-f]{32}"));
        assertEquals("sec:", Ids.sectionLocalKey(List.of(), List.of()));
    }

    @Test
    void jcsCanonicalizes() {
        var node = JsonMapper.shared().readTree("{\"b\": 1.0, \"a\": \"ü\"}");
        assertArrayEquals("{\"a\":\"ü\",\"b\":1}".getBytes(StandardCharsets.UTF_8), Jcs.canonicalize(node));
    }

    @Test
    void contractsReportAllMessages() {
        var instance = JsonMapper.shared().readTree("{\"projectId\": \"Bad Id\", \"extra\": 1}");
        var e = assertThrows(
                Contracts.ContractException.class,
                () -> CONTRACTS.validate("skills.schema.json#/$defs/PluginDetectInput", instance));
        assertEquals("skills.schema.json#/$defs/PluginDetectInput", e.ref());
        String all = String.join("\n", e.messages());
        assertTrue(e.messages().size() >= 4, all); // pattern, 2x required, additional property
        assertTrue(all.contains("llmPolicy") && all.contains("queryServiceUrl") && all.contains("extra"), all);
        var valid = JsonMapper.shared()
                .readTree(
                        "{\"projectId\": \"mini\", \"llmPolicy\": \"local-only\", \"queryServiceUrl\": \"http://q\"}");
        assertTrue(CONTRACTS.isValid(
                "https://sandman.invalid/contracts/skills.schema.json#/$defs/PluginDetectInput", valid));
        // 'format' is an annotation only, as in the Python library
        ((tools.jackson.databind.node.ObjectNode) valid).put("queryServiceUrl", "not a uri");
        assertTrue(CONTRACTS.isValid("skills.schema.json#/$defs/PluginDetectInput", valid));
    }

    @Test
    void llmAccessEnforcesLocalOnly(@TempDir Path tmp) throws Exception {
        Path models = tmp.resolve("models.yaml");
        Files.writeString(models, "routes: {chat-default: openai/m, chat-local: ollama/m:1, embed-local: ollama/e}\n");
        Files.writeString(tmp.resolve("cloud"), "cloud-key");
        Files.writeString(tmp.resolve("local"), "local-key");
        Map<String, String> env = Map.of(
                "SANDMAN_SERVICE_NAME", "tlr-agent",
                "SANDMAN_LLM_BASE_URL", "http://llm-replay:8000/openai",
                "SANDMAN_LLM_MODELS_FILE", models.toString(),
                "SANDMAN_LLM_KEY_FILE", tmp.resolve("cloud").toString(),
                "SANDMAN_LLM_LOCAL_KEY_FILE", tmp.resolve("local").toString());
        LlmAccess local = LlmAccess.forPolicy(Settings.fromEnv(env), "local-only");
        assertEquals("local-key", local.apiKey());
        assertEquals("http://llm-replay:8000/openai", local.baseUrl());
        assertEquals("ollama/m:1", local.chatModel());
        assertEquals("ollama/e", local.embedModel());
        assertThrows(LlmAccess.PolicyException.class, () -> local.model("chat-default"));
        LlmAccess cloud = LlmAccess.forPolicy(Settings.fromEnv(env), "cloud-allowed");
        assertEquals("cloud-key", cloud.apiKey());
        assertEquals("openai/m", cloud.chatModel());
        assertThrows(LlmAccess.PolicyException.class, cloud::embedModel); // embed-default not configured
        var noLocal = new java.util.HashMap<>(env);
        noLocal.remove("SANDMAN_LLM_LOCAL_KEY_FILE");
        assertThrows(
                LlmAccess.PolicyException.class, () -> LlmAccess.forPolicy(Settings.fromEnv(noLocal), "local-only"));
        assertFalse(local.toString().contains("local-key"));
    }

    @Test
    void serviceAuthVerifiesTokensAndForwarders() {
        var auth = new ServiceAuth(
                Map.of("query-service", Ids.sha256Hex("qs"), "plugin:x", Ids.sha256Hex("p")), "own-token");
        assertEquals("own-token", auth.ownToken());
        assertEquals("query-service", auth.callerFromHeader("Bearer qs"));
        assertNull(auth.callerFromHeader("Bearer other"));
        assertNull(auth.caller(null));
        assertThrows(ServiceAuth.UnauthorizedException.class, () -> auth.requireService("Bearer wrong", false));
        assertThrows(ServiceAuth.UnauthorizedException.class, () -> auth.requireService(null, false));
        assertThrows(ServiceAuth.ForbiddenException.class, () -> auth.requireService("Bearer p", false));
        assertEquals("plugin:x", auth.requireService("bearer p", true));
        assertEquals("user:bob", ServiceAuth.trustedActor("query-service", "user:bob"));
        assertNull(ServiceAuth.trustedActor("tlr-agent", null));
        assertThrows(ServiceAuth.ForbiddenException.class, () -> ServiceAuth.trustedActor("tlr-agent", "user:bob"));
        assertEquals("user:bob", ServiceAuth.actorFromMetadata("orchestrator", Map.of("sandman.actor", "user:bob")));
        assertNull(ServiceAuth.actorFromMetadata("tlr-agent", Map.of("sandman.actor", "user:bob")));
    }

    @Test
    void artifactStoreIsContentAddressedAndRejectsTraversal(@TempDir Path tmp) throws Exception {
        var store = new ArtifactStore(tmp);
        var stored = store.put("mini", "hello".getBytes(StandardCharsets.UTF_8));
        assertEquals("artifact://mini/" + stored.sha256(), stored.uri());
        assertEquals(stored, store.put("mini", "hello".getBytes(StandardCharsets.UTF_8)));
        try (InputStream in = store.open(stored.uri())) {
            assertEquals("hello", new String(in.readAllBytes(), StandardCharsets.UTF_8));
        }
        for (String bad : List.of("artifact://mini/../../etc", "artifact://Mini/" + stored.sha256())) {
            assertThrows(IllegalArgumentException.class, () -> store.pathFor(bad));
        }
        assertThrows(IllegalArgumentException.class, () -> store.put("../x", new byte[0]));
        try (var files = Files.list(tmp.resolve("mini"))) {
            assertEquals(1, files.count()); // no temp files left
        }
    }

    @Test
    void statusTrackerReportsWorkingWhileTasksRun() {
        var tracker = new StatusTracker("tlr-agent", "1.0");
        var idle = tracker.snapshot(null);
        assertEquals("idle", idle.get("state").asString());
        assertFalse(idle.has("lastActivityAt"));
        try (var a = tracker.track();
                var b = tracker.track()) {
            var working = tracker.snapshot(JsonMapper.shared().readTree("{\"pending\": 1}"));
            assertEquals("working", working.get("state").asString());
            assertEquals(2, working.get("activeTasks").asInt());
            assertEquals(1, working.get("details").get("pending").asInt());
            a.close(); // closing twice counts once
        }
        var after = tracker.snapshot(JsonMapper.shared().createObjectNode());
        assertEquals(0, after.get("activeTasks").asInt());
        assertEquals("idle", after.get("state").asString());
        assertTrue(after.get("lastActivityAt").asString().endsWith("Z"));
        assertFalse(after.has("details"));
    }
}
