package edu.kit.kastel.mcse.ardoco.sandman.inconsistency;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import edu.kit.kastel.mcse.ardoco.sandman.agent.Contracts;
import edu.kit.kastel.mcse.ardoco.sandman.agent.KgClient;
import edu.kit.kastel.mcse.ardoco.sandman.agent.RunRecorder;
import edu.kit.kastel.mcse.ardoco.sandman.agent.Settings;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIf;
import org.junit.jupiter.api.io.TempDir;

/** Detector params, whole-project scope and the configHash of the run identity (T3.1 steps 2 to 5). */
@EnabledIf("edu.kit.kastel.mcse.ardoco.sandman.inconsistency.Docker#available")
class WholeProjectTest {

    static final String PROJECT = "mini";
    static KgClient kg;
    static Contracts contracts;

    final ScriptedDetector scripted = new ScriptedDetector();
    DetectionService service;
    String docArt;
    String modelArt;
    String modelKey;

    @BeforeAll
    static void connect() {
        kg = TestKg.client();
        contracts = new Contracts(Settings.defaultContractsDir());
    }

    @AfterAll
    static void close() {
        if (kg != null) {
            kg.close();
        }
    }

    @BeforeEach
    void seed() {
        TestKg.reset(kg, PROJECT);
        docArt = TestKg.artifact(kg, PROJECT, "doc.md", "architecture-doc");
        modelArt = TestKg.artifact(kg, PROJECT, "model.repository", "architecture-model");
        modelKey = TestKg.element(kg, PROJECT, modelArt, "ModelElement", "m1", "h1");
        scripted.findings = List.of(ScriptedDetector.finding(modelKey));
        service = service(new DetectorRegistry(List.of(scripted)));
    }

    DetectionService service(DetectorRegistry registry) {
        return new DetectionService(
                kg,
                contracts,
                new RunRecorder(kg, "inconsistency-agent", "test"),
                registry,
                "http://query-service:8000",
                n -> {});
    }

    DetectionService.Result detect(List<String> scope) throws Exception {
        return service.detect(new DetectionService.Request(PROJECT, scope, List.of("scripted"), null, null, null));
    }

    String status() {
        return (String) kg.read("MATCH (i:Inconsistency) RETURN i.status AS s", Map.of())
                .get(0)
                .get("s");
    }

    @Test
    void pairwiseDetectorKeepsItemOutsideScope() throws Exception {
        detect(null);
        scripted.findings = List.of();
        detect(List.of(docArt));
        assertEquals("OPEN", status());
    }

    @Test
    void wholeProjectDetectorResolvesItemOutsideScope() throws Exception {
        scripted.mode = InconsistencyDetector.ScopeMode.WHOLE_PROJECT;
        detect(null);
        scripted.findings = List.of();
        var r = detect(List.of(docArt));
        assertEquals(1, r.output().get("autoResolvedIds").size());
        assertEquals("RESOLVED", status());
    }

    @Test
    void contextCarriesParamsAndCancelFlag() throws Exception {
        var registry = new DetectorRegistry(List.of(scripted));
        service = service(registry);
        detect(null);
        assertEquals(Map.of(), scripted.seen.params());
        assertEquals(false, scripted.seen.cancelled().getAsBoolean());
    }

    @Test
    void configHashChangesRunKey() throws Exception {
        String a = runKey(detect(null));
        scripted.identity = "provider-1";
        String b = runKey(detect(null));
        scripted.identity = "provider-2";
        String c = runKey(detect(null));
        assertNotEquals(a, b);
        assertNotEquals(b, c);
        scripted.identity = "provider-2";
        assertEquals(c, runKey(detect(null)));
    }

    String runKey(DetectionService.Result r) {
        return (String) kg.read(
                        "MATCH (r:Run {id: $id}) RETURN r.inputKey AS k",
                        Map.of("id", r.output().get("runId").asString()))
                .get(0)
                .get("k");
    }

    @Test
    void registryPassesParamsAndRejectsUnknown(@TempDir Path dir) throws Exception {
        Path ok = dir.resolve("ok.yaml");
        Files.writeString(ok, "detectors:\n  stub-untraced: { enabled: true }\n");
        assertEquals(Map.of(), DetectorRegistry.load(ok, contracts).params("stub-untraced"));
        Path bad = dir.resolve("bad.yaml");
        Files.writeString(bad, "detectors:\n  stub-untraced: { enabled: true, params: { x: 1 } }\n");
        var e = assertThrows(Settings.ConfigException.class, () -> DetectorRegistry.load(bad, contracts));
        assertEquals(true, e.getMessage().contains("unknown param 'x'"));
    }
}
