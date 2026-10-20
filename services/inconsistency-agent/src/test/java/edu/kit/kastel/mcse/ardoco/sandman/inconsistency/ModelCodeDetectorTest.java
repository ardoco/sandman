package edu.kit.kastel.mcse.ardoco.sandman.inconsistency;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import edu.kit.kastel.mcse.ardoco.sandman.agent.Contracts;
import edu.kit.kastel.mcse.ardoco.sandman.agent.KgClient;
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

@EnabledIf("edu.kit.kastel.mcse.ardoco.sandman.inconsistency.Docker#available")
class ModelCodeDetectorTest {

    static final String PROJECT = "mc";
    static KgClient kg;

    String modelArt;
    String codeArt;
    String testArt;

    @BeforeAll
    static void connect() {
        kg = TestKg.client();
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
        modelArt = TestKg.artifact(kg, PROJECT, "model.repository", "architecture-model");
        codeArt = TestKg.artifact(kg, PROJECT, "src/A.java", "code");
        testArt = TestKg.artifact(kg, PROJECT, "test/ATest.java", "test");
    }

    String component(String name, boolean inferred) {
        String key = TestKg.element(kg, PROJECT, modelArt, "ModelElement", name, "h");
        kg.write(
                "MATCH (e:ModelElement {key: $k}) SET e.kind = 'component', e.inferred = $i",
                Map.of("k", key, "i", inferred));
        return key;
    }

    String code(String artifact, String localKey, String kind) {
        String key = TestKg.element(kg, PROJECT, artifact, "CodeUnit", localKey, "h");
        kg.write("MATCH (e:CodeUnit {key: $k}) SET e.kind = $kind", Map.of("k", key, "kind", kind));
        return key;
    }

    void contains(String parent, String child) {
        kg.write(
                "MATCH (a:Element {key: $a}), (b:Element {key: $b}) CREATE (a)-[:CONTAINS]->(b)",
                Map.of("a", parent, "b", child));
    }

    void link(String model, String code, String approach, String status) {
        kg.write(
                "MATCH (s:Element {key: $s}), (t:Element {key: $t}) "
                        + "CREATE (l:TraceLink {id: $id, projectId: $p, linkType: 'implements', confidence: 1.0, approach: $a, "
                        + "approachVersion: '0', configHash: 'c', status: $status, active: true, runId: 'run_x'}) "
                        + "CREATE (l)-[:SOURCE]->(s), (l)-[:TARGET]->(t)",
                Map.of("s", model, "t", code, "id", model + code, "p", PROJECT, "a", approach, "status", status));
    }

    InconsistencyDetector.Detection run(Map<String, Object> params) {
        var scope = new DetectionScope(PROJECT, java.util.Set.of(), true);
        var ctx = new DetectorContext(kg, "local-only", null, params, null, () -> false);
        return new ModelCodeDetector().detectWithWarnings(scope, ctx);
    }

    List<String> subjects(InconsistencyDetector.Detection d, String type) {
        return d.findings().stream()
                .filter(f -> f.type().equals(type))
                .map(f -> f.involved().getFirst().elementKey())
                .toList();
    }

    @Test
    void linkedComponentAndFileGiveNoFinding() {
        link(component("c1", false), code(codeArt, "src/A.java", "file"), "transarc", "proposed");
        var d = run(Map.of());
        assertEquals(List.of(), d.findings());
        assertEquals(List.of(), d.warnings());
    }

    @Test
    void rejectedLinkGivesBothFindings() {
        String c1 = component("c1", false);
        String f1 = code(codeArt, "src/A.java", "file");
        link(c1, f1, "transarc", "rejected");
        link(component("c2", false), code(codeArt, "src/B.java", "file"), "transarc", "proposed");
        var d = run(Map.of());
        assertEquals(List.of(c1), subjects(d, "model-element-without-code"));
        assertEquals(List.of(f1), subjects(d, "code-without-model-element"));
        var model = d.findings().stream()
                .filter(f -> f.type().equals("model-element-without-code"))
                .findFirst()
                .orElseThrow();
        assertEquals("warning", model.severity());
        assertEquals("Model element without code: c1", model.title());
        assertEquals(false, model.evidence().get("inferredModel"));
    }

    @Test
    void linkToTypeCountsForItsFile() {
        String file = code(codeArt, "src/A.java", "file");
        String type = code(codeArt, "A", "type");
        contains(file, type);
        link(component("c1", false), type, "transarc", "proposed");
        var d = run(Map.of());
        assertEquals(List.of(), d.findings());
    }

    @Test
    void inferredComponentIsInfo() {
        link(component("c1", false), code(codeArt, "src/A.java", "file"), "transarc", "proposed");
        component("c2", true);
        var d = run(Map.of());
        var f = d.findings().getFirst();
        assertEquals("info", f.severity());
        assertEquals(true, f.evidence().get("inferredModel"));
    }

    @Test
    void excludedGlobAndTestArtifactGiveNoFinding() {
        link(component("c1", false), code(codeArt, "src/A.java", "file"), "transarc", "proposed");
        code(codeArt, "src/package-info.java", "file");
        code(testArt, "test/ATest.java", "file");
        assertEquals(List.of(), run(Map.of()).findings());
        String extra = code(codeArt, "src/pkg/B.java", "file");
        assertEquals(List.of(extra), subjects(run(Map.of()), "code-without-model-element"));
    }

    @Test
    void linkApproachesFilter() {
        String c1 = component("c1", false);
        link(c1, code(codeArt, "src/A.java", "file"), "stub-name-match", "proposed");
        link(component("c2", false), code(codeArt, "src/B.java", "file"), "transarc", "proposed");
        assertEquals(List.of(), run(Map.of()).findings());
        var d = run(Map.of("linkApproaches", List.of("transarc")));
        assertEquals(List.of(c1), subjects(d, "model-element-without-code"));
    }

    @Test
    void noLinksGivesWarningOnly() {
        component("c1", false);
        code(codeArt, "src/A.java", "file");
        var d = run(Map.of());
        assertEquals(List.of(), d.findings());
        assertEquals(
                List.of("model-code: no model-code links; enable transarc or declare links (T7.2)"), d.warnings());
    }

    @Test
    void capsFindingsPerType() {
        link(component("c0", false), code(codeArt, "src/A.java", "file"), "transarc", "proposed");
        component("c1", false);
        component("c2", false);
        component("c3", false);
        var d = run(Map.of("maxFindingsPerType", 2));
        assertEquals(2, subjects(d, "model-element-without-code").size());
        assertEquals(List.of("model-code: model-element-without-code capped at 2 of 3"), d.warnings());
    }

    @Test
    void runsAreDeterministic() {
        link(component("c1", false), code(codeArt, "src/A.java", "file"), "transarc", "rejected");
        component("c2", false);
        assertEquals(run(Map.of()).findings(), run(Map.of()).findings());
    }

    @Test
    void registryLoadsDetectorAndRejectsUnknownParam(@TempDir Path dir) throws Exception {
        var contracts = new Contracts(Settings.defaultContractsDir());
        Path yaml = dir.resolve("plugins.yaml");
        Files.writeString(yaml, "detectors:\n  model-code: { enabled: true, params: { codeKinds: [code] } }\n");
        assertEquals(
                List.of("model-code"),
                DetectorRegistry.load(yaml, contracts).all().stream()
                        .map(InconsistencyDetector::id)
                        .toList());
        Files.writeString(yaml, "detectors:\n  model-code: { enabled: true, params: { bogus: 1 } }\n");
        var e = assertThrows(Settings.ConfigException.class, () -> DetectorRegistry.load(yaml, contracts));
        assertTrue(e.getMessage().contains("unknown param 'bogus'"), e.getMessage());
    }
}
