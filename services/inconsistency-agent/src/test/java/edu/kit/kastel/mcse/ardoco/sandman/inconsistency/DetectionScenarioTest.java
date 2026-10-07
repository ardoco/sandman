package edu.kit.kastel.mcse.ardoco.sandman.inconsistency;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import edu.kit.kastel.mcse.ardoco.sandman.agent.Contracts;
import edu.kit.kastel.mcse.ardoco.sandman.agent.Event;
import edu.kit.kastel.mcse.ardoco.sandman.agent.Ids;
import edu.kit.kastel.mcse.ardoco.sandman.agent.KgClient;
import edu.kit.kastel.mcse.ardoco.sandman.agent.RunRecorder;
import edu.kit.kastel.mcse.ardoco.sandman.agent.Settings;
import edu.kit.kastel.mcse.ardoco.sandman.inconsistency.Lifecycle.Status;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Set;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIf;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.node.ObjectNode;

@EnabledIf("edu.kit.kastel.mcse.ardoco.sandman.inconsistency.Docker#available")
class DetectionScenarioTest {

    static final String PROJECT = "mini";
    static KgClient kg;
    static Contracts contracts;

    final ScriptedDetector scripted = new ScriptedDetector();
    final List<ObjectNode> notifications = new ArrayList<>();
    DetectionService service;
    TriageService triage;
    String reqArt;
    String modelArt;
    String req1;
    String req2;
    String model1;

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
        reqArt = TestKg.artifact(kg, PROJECT, "requirements.md", "requirements");
        modelArt = TestKg.artifact(kg, PROJECT, "model.repository", "architecture-model");
        req1 = TestKg.element(kg, PROJECT, reqArt, "Requirement", "REQ-1", "h1");
        req2 = TestKg.element(kg, PROJECT, reqArt, "Requirement", "REQ-2", "h2");
        model1 = TestKg.element(kg, PROJECT, modelArt, "ModelElement", "M1", "hm");
        service = new DetectionService(
                kg,
                contracts,
                new RunRecorder(kg, "inconsistency-agent", "test"),
                new DetectorRegistry(List.of(new StubUntracedDetector(), scripted)),
                "http://query-service:8000",
                notifications::add);
        triage = new TriageService(kg, (type, subject, data, corr, cause, id) -> null);
    }

    DetectionService.Result detect(List<String> scope, List<String> detectors) throws Exception {
        var result = service.detect(new DetectionService.Request(PROJECT, scope, detectors, null, null, null));
        contracts.validate("skills.schema.json#/$defs/DetectInconsistenciesOutput", result.output());
        Event.build(
                contracts,
                "inconsistency-agent",
                "sandman.inconsistencies.updated",
                PROJECT,
                result.eventData(),
                null,
                "msg-1",
                null);
        return result;
    }

    static List<String> ids(JsonNode array) {
        List<String> ids = new ArrayList<>();
        array.forEach(n -> ids.add(n.asString()));
        return ids;
    }

    static String stubId(String key) {
        return Ids.stableId("inc", PROJECT, Ids.fingerprint("untraced-requirement", "stub-untraced", List.of(key)));
    }

    static String scriptedId(String... keys) {
        return Ids.stableId("inc", PROJECT, Ids.fingerprint("scripted-finding", "scripted", List.of(keys)));
    }

    Map<String, Object> inc(String id) {
        return kg.read(
                        "MATCH (i:Inconsistency {id: $id}) RETURN i.status AS status, i.verification AS verification, "
                                + "i.lastRunId AS lastRunId, i.detectorVersion AS version",
                        Map.of("id", id))
                .get(0);
    }

    String status(String id) {
        return (String) inc(id).get("status");
    }

    List<String> reasons(String id) {
        return kg
                .read(
                        "MATCH (:Inconsistency {id: $id})-[:HAS_STATUS_CHANGE]->(c) RETURN c.reason AS r ORDER BY c.at, id(c)",
                        Map.of("id", id))
                .stream()
                .map(m -> (String) m.get("r"))
                .toList();
    }

    void user(String id, Status to, Status expected) {
        triage.transition(id, to, expected, "because", "user:alice");
    }

    @Test
    void lifecycleScenario() throws Exception {
        var r1 = detect(null, List.of("stub-untraced"));
        assertEquals(
                Set.of(stubId(req1), stubId(req2)), Set.copyOf(ids(r1.output().get("newIds"))));
        assertEquals(2, notifications.size());
        assertEquals("notification", notifications.get(0).get("event").asString());
        assertEquals("OPEN", notifications.get(0).get("status").asString());
        assertEquals(2, r1.output().get("stillOpenCount").asInt());

        user(stubId(req1), Status.DEFERRED, Status.OPEN);
        TestKg.link(kg, PROJECT, req2, model1, true, "candidate"); // req2 now traced

        var r2 = detect(null, List.of("stub-untraced"));
        assertEquals(List.of(stubId(req2)), ids(r2.output().get("autoResolvedIds")));
        assertEquals("RESOLVED", status(stubId(req2)));
        assertEquals("confirmed", inc(stubId(req2)).get("verification"));
        assertEquals(List.of("not-redetected"), reasons(stubId(req2)));
        assertEquals("DEFERRED", status(stubId(req1)));
        assertEquals(r2.output().get("runId").asString(), inc(stubId(req1)).get("lastRunId"));

        // accepted item reopens only if the evidence content changed
        String req3 = TestKg.element(kg, PROJECT, reqArt, "Requirement", "REQ-3", "h3");
        var r3 = detect(null, List.of("stub-untraced"));
        assertEquals(List.of(stubId(req3)), ids(r3.output().get("newIds")));
        user(stubId(req3), Status.ACCEPTED, Status.OPEN);
        var r4 = detect(null, List.of("stub-untraced"));
        assertEquals("ACCEPTED", status(stubId(req3)));
        assertEquals(r4.output().get("runId").asString(), inc(stubId(req3)).get("lastRunId"));
        kg.write("MATCH (e:Element {key: $k}) SET e.contentHash = 'changed'", Map.of("k", req3));
        notifications.clear();
        var r5 = detect(null, List.of("stub-untraced"));
        assertEquals(List.of(stubId(req3)), ids(r5.output().get("reopenedIds")));
        assertEquals("REOPENED", status(stubId(req3)));
        assertEquals("REOPENED", notifications.get(0).get("status").asString());
        assertEquals("re-detected", reasons(stubId(req3)).get(1));
    }

    @Test
    void rejectedLinkDoesNotTraceARequirement() throws Exception {
        TestKg.link(kg, PROJECT, req1, model1, true, "rejected");
        TestKg.link(kg, PROJECT, req2, model1, false, "candidate"); // inactive
        var r = detect(null, List.of("stub-untraced"));
        assertEquals(2, r.output().get("newIds").size());
    }

    @Test
    void failingDetectorIsPartialAndNeverAutoResolves() throws Exception {
        scripted.findings = List.of(ScriptedDetector.finding(req1));
        detect(null, List.of("scripted"));
        scripted.error = new IllegalStateException("boom");
        var r = detect(null, null);
        assertEquals("partial", r.output().get("status").asString());
        assertEquals(0, r.output().get("autoResolvedIds").size());
        assertEquals("OPEN", status(scriptedId(req1)));
        JsonNode runs = r.output().get("detectorRuns");
        assertEquals("completed", runs.get(0).get("status").asString());
        assertEquals("failed", runs.get(1).get("status").asString());
        assertEquals("boom", runs.get(1).get("error").asString());
        assertEquals(
                "partial",
                kg.read(
                                "MATCH (r:Run {id: $id}) RETURN r.status AS s",
                                Map.of("id", r.output().get("runId").asString()))
                        .get(0)
                        .get("s"));
    }

    @Test
    void detectorSubsetLeavesOtherDetectorsItemsUntouched() throws Exception {
        var r1 = detect(null, List.of("stub-untraced"));
        var r2 = detect(null, List.of("scripted"));
        assertEquals(0, r2.output().get("autoResolvedIds").size());
        assertEquals("OPEN", status(stubId(req1)));
        assertEquals(r1.output().get("runId").asString(), inc(stubId(req1)).get("lastRunId"));
    }

    @Test
    void itemsPartlyOutsideScopeAreUntouched() throws Exception {
        detect(null, List.of("stub-untraced"));
        TestKg.link(kg, PROJECT, req1, model1, true, "candidate");
        detect(List.of(modelArt), List.of("stub-untraced"));
        assertEquals("OPEN", status(stubId(req1)));
        scripted.findings = List.of(ScriptedDetector.finding(req1, model1)); // spans two artifacts
        detect(null, List.of("scripted"));
        scripted.findings = List.of();
        detect(List.of(reqArt), List.of("scripted"));
        assertEquals("OPEN", status(scriptedId(req1, model1)));
        var r = detect(List.of(reqArt, modelArt), List.of("scripted", "stub-untraced"));
        assertTrue(ids(r.output().get("autoResolvedIds")).containsAll(List.of(scriptedId(req1, model1), stubId(req1))));
    }

    @Test
    void deletedArtifactResolvesIndependentOfScope() throws Exception {
        detect(null, List.of("stub-untraced"));
        kg.write("MATCH (:Artifact {id: $a})-[c:CURRENT_VERSION]->() DELETE c", Map.of("a", reqArt));
        var r = detect(List.of(modelArt), List.of("scripted"));
        assertEquals(
                Set.of(stubId(req1), stubId(req2)), Set.copyOf(ids(r.output().get("autoResolvedIds"))));
        assertEquals(List.of("artifact-deleted"), reasons(stubId(req1)));
        assertEquals("confirmed", inc(stubId(req1)).get("verification"));
    }

    @Test
    void detectorVersionBumpKeepsFingerprintsAndStatuses() throws Exception {
        scripted.findings = List.of(ScriptedDetector.finding(req1));
        detect(null, List.of("scripted"));
        user(scriptedId(req1), Status.ACCEPTED, Status.OPEN);
        scripted.version = "2";
        var r = detect(null, List.of("scripted"));
        assertEquals(0, r.output().get("newIds").size());
        assertEquals(0, r.output().get("reopenedIds").size());
        assertEquals("ACCEPTED", status(scriptedId(req1)));
        assertEquals("2", inc(scriptedId(req1)).get("version"));
    }

    @Test
    void concurrentUserTransitionWins() throws Exception {
        scripted.findings = List.of(ScriptedDetector.finding(req1), ScriptedDetector.finding(req2));
        var first = detect(null, List.of("scripted"));
        scripted.findings = List.of(ScriptedDetector.finding(req1)); // req2 would be auto-resolved
        scripted.onDetect = () -> {
            user(scriptedId(req2), Status.ACCEPTED, Status.OPEN);
            user(scriptedId(req1), Status.DEFERRED, Status.OPEN);
        };
        var r = detect(null, List.of("scripted"));
        assertEquals(0, r.output().get("autoResolvedIds").size());
        assertEquals("ACCEPTED", status(scriptedId(req2)));
        assertEquals("DEFERRED", status(scriptedId(req1)));
        assertEquals(
                first.output().get("runId").asString(), inc(scriptedId(req1)).get("lastRunId"));
        assertNotEquals(
                r.output().get("runId").asString(), inc(scriptedId(req1)).get("lastRunId"));
    }

    @Test
    void unknownElementKeyFailsTheDetector() throws Exception {
        scripted.findings = List.of(ScriptedDetector.finding("art_00000000000000000000000000000000#nope"));
        var r = detect(null, List.of("scripted"));
        assertEquals(
                "failed", r.output().get("detectorRuns").get(0).get("status").asString());
        assertEquals("partial", r.output().get("status").asString());
    }
}
