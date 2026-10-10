package edu.kit.kastel.mcse.ardoco.sandman.inconsistency;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import edu.kit.kastel.mcse.ardoco.sandman.agent.Contracts;
import edu.kit.kastel.mcse.ardoco.sandman.agent.Ids;
import edu.kit.kastel.mcse.ardoco.sandman.agent.KgClient;
import edu.kit.kastel.mcse.ardoco.sandman.agent.RunRecorder;
import edu.kit.kastel.mcse.ardoco.sandman.agent.Settings;
import edu.kit.kastel.mcse.ardoco.sandman.inconsistency.Lifecycle.Status;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIf;

/** One test per row of the normative transition table (adr/0007, amendment 2026-10-09, R-018). */
@EnabledIf("edu.kit.kastel.mcse.ardoco.sandman.inconsistency.Docker#available")
class TransitionTableTest {

    static final String PROJECT = "mini";
    static KgClient kg;
    static Contracts contracts;

    final ScriptedDetector scripted = new ScriptedDetector();
    DetectionService service;
    TriageService triage;
    String reqArt;
    String modelArt;
    String req1;
    String id;

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
        service = new DetectionService(
                kg,
                contracts,
                new RunRecorder(kg, "inconsistency-agent", "test"),
                new DetectorRegistry(List.of(scripted)),
                "http://query-service:8000",
                n -> {});
        triage = new TriageService(kg, (type, subject, data, corr, cause, eid) -> null);
        scripted.findings = List.of(ScriptedDetector.finding(req1));
        id = Ids.stableId("inc", PROJECT, Ids.fingerprint("scripted-finding", "scripted", List.of(req1)));
    }

    DetectionService.Result detect(List<String> scope) throws Exception {
        var r = service.detect(new DetectionService.Request(PROJECT, scope, List.of("scripted"), null, null, null));
        contracts.validate("skills.schema.json#/$defs/DetectInconsistenciesOutput", r.output());
        return r;
    }

    void force(Status status, String verification) {
        kg.write(
                "MATCH (i:Inconsistency {id: $id}) SET i.status = $s, i.verification = $v",
                Map.of("id", id, "s", status.name(), "v", verification));
    }

    void forceAccepted(Status status) {
        kg.write(
                "MATCH (i:Inconsistency {id: $id}) SET i.status = $s, i.verification = 'none', "
                        + "i.acceptedEvidenceHash = i.evidenceHash",
                Map.of("id", id, "s", status.name()));
    }

    Map<String, Object> inc() {
        return kg.read(
                        "MATCH (i:Inconsistency {id: $id}) RETURN i.status AS status, i.verification AS verification, "
                                + "i.evidenceHash AS hash",
                        Map.of("id", id))
                .get(0);
    }

    List<Map<String, Object>> changes() {
        return kg.read(
                "MATCH (:Inconsistency {id: $id})-[:HAS_STATUS_CHANGE]->(c) "
                        + "RETURN c.from AS from, c.to AS to, c.actor AS actor, c.reason AS reason ORDER BY c.at, id(c)",
                Map.of("id", id));
    }

    void assertLastChange(Object from, String to, String actor, String reason) {
        var c = changes().getLast();
        assertEquals(from, c.get("from"));
        assertEquals(to, c.get("to"));
        assertEquals(actor, c.get("actor"));
        assertEquals(reason, c.get("reason"));
    }

    @Test
    void row1NewFindingCreatesOpenWithCreationEntryWithoutFrom() throws Exception {
        var r = detect(null);
        assertEquals(
                List.of(id),
                r.output().get("newIds").valueStream().map(n -> n.asString()).toList());
        assertEquals("OPEN", inc().get("status"));
        assertEquals("none", inc().get("verification"));
        assertEquals(1, changes().size());
        assertLastChange(null, "OPEN", "system:scripted", "detected");
        assertEquals(Ids.evidenceHash(List.of(req1 + "=h1")), inc().get("hash"));
    }

    @Test
    void row2RedetectedOpenReopenedDeferredStaySameWithoutStatusChange() throws Exception {
        detect(null);
        for (Status s : List.of(Status.OPEN, Status.REOPENED, Status.DEFERRED)) {
            force(s, "none");
            var r = detect(null);
            assertEquals(s.name(), inc().get("status"));
            assertEquals(0, r.output().get("reopenedIds").size());
            assertEquals(1, changes().size(), s.name());
        }
    }

    @Test
    void row3RedetectedResolvedReopensForAnyVerification() throws Exception {
        detect(null);
        for (String verification : List.of("pending", "confirmed")) {
            force(Status.RESOLVED, verification);
            int before = changes().size();
            var r = detect(null);
            assertEquals(1, r.output().get("reopenedIds").size(), verification);
            assertEquals("REOPENED", inc().get("status"));
            assertEquals("none", inc().get("verification"));
            assertEquals(before + 1, changes().size());
            assertLastChange("RESOLVED", "REOPENED", "system:scripted", "re-detected");
        }
    }

    @Test
    void row4AcceptedRejectedReopenWhenEvidenceHashChanged() throws Exception {
        detect(null);
        for (Status s : List.of(Status.ACCEPTED, Status.REJECTED)) {
            forceAccepted(s);
            kg.write("MATCH (e:Element {key: $k}) SET e.contentHash = $h", Map.of("k", req1, "h", "h-" + s));
            var r = detect(null);
            assertEquals(1, r.output().get("reopenedIds").size(), s.name());
            assertEquals("REOPENED", inc().get("status"));
            assertEquals("none", inc().get("verification"));
            assertLastChange(s.name(), "REOPENED", "system:scripted", "re-detected");
        }
    }

    @Test
    void row5AcceptedRejectedUntouchedWhenEvidenceHashEqual() throws Exception {
        detect(null);
        for (Status s : List.of(Status.ACCEPTED, Status.REJECTED)) {
            forceAccepted(s);
            int before = changes().size();
            var r = detect(null);
            assertEquals(0, r.output().get("reopenedIds").size());
            assertEquals(s.name(), inc().get("status"));
            assertEquals(before, changes().size());
        }
    }

    @Test
    void row6NotRedetectedResolvesAutoResolvableStatesAndConfirmsPendingResolved() throws Exception {
        detect(null);
        scripted.findings = List.of();
        for (var state : List.of(
                Map.entry(Status.OPEN, "none"),
                Map.entry(Status.REOPENED, "none"),
                Map.entry(Status.DEFERRED, "none"),
                Map.entry(Status.RESOLVED, "pending"))) {
            force(state.getKey(), state.getValue());
            var r = detect(null);
            assertEquals(
                    1, r.output().get("autoResolvedIds").size(), state.getKey().name());
            assertEquals("RESOLVED", inc().get("status"));
            assertEquals("confirmed", inc().get("verification"));
            assertLastChange(state.getKey().name(), "RESOLVED", "system:scripted", "not-redetected");
        }
    }

    @Test
    void row7DeletedArtifactResolvesIndependentOfScope() throws Exception {
        detect(null);
        scripted.findings = List.of();
        force(Status.RESOLVED, "pending");
        kg.write("MATCH (:Artifact {id: $a})-[c:CURRENT_VERSION]->() DELETE c", Map.of("a", reqArt));
        var r = detect(List.of(modelArt));
        assertEquals(1, r.output().get("autoResolvedIds").size());
        assertEquals("confirmed", inc().get("verification"));
        assertLastChange("RESOLVED", "RESOLVED", "system:scripted", "artifact-deleted");
    }

    @Test
    void row8NotRedetectedAcceptedRejectedConfirmedResolvedAreUntouched() throws Exception {
        detect(null);
        scripted.findings = List.of();
        for (var state : List.of(
                Map.entry(Status.ACCEPTED, "none"),
                Map.entry(Status.REJECTED, "none"),
                Map.entry(Status.RESOLVED, "confirmed"))) {
            force(state.getKey(), state.getValue());
            int before = changes().size();
            var r = detect(null);
            assertEquals(0, r.output().get("autoResolvedIds").size());
            assertEquals(state.getKey().name(), inc().get("status"));
            assertEquals(state.getValue(), inc().get("verification"));
            assertEquals(before, changes().size());
        }
    }

    @Test
    void row9OpenReopenedUserTransitions() throws Exception {
        detect(null);
        for (Status from : List.of(Status.OPEN, Status.REOPENED)) {
            for (Status to : List.of(Status.DEFERRED, Status.ACCEPTED, Status.REJECTED, Status.RESOLVED)) {
                force(from, "none");
                triage.transition(id, to, from, "because", "user:alice");
                assertEquals(to.name(), inc().get("status"));
                assertEquals(to == Status.RESOLVED ? "pending" : "none", inc().get("verification"));
                assertLastChange(from.name(), to.name(), "user:alice", "because");
                assertEquals(
                        to == Status.ACCEPTED || to == Status.REJECTED,
                        kg.read(
                                                "MATCH (i:Inconsistency {id: $id}) RETURN i.acceptedEvidenceHash AS h",
                                                Map.of("id", id))
                                        .get(0)
                                        .get("h")
                                != null);
                kg.write("MATCH (i:Inconsistency {id: $id}) REMOVE i.acceptedEvidenceHash", Map.of("id", id));
            }
        }
        force(Status.OPEN, "none");
        assertEquals(422, reject(Status.OPEN, Status.DEFERRED, null));
    }

    @Test
    void row10DeferredUserTransitions() throws Exception {
        detect(null);
        for (Status to : List.of(Status.OPEN, Status.ACCEPTED, Status.REJECTED, Status.RESOLVED)) {
            force(Status.DEFERRED, "none");
            triage.transition(id, to, Status.DEFERRED, "because", "user:alice");
            assertEquals(to.name(), inc().get("status"));
            assertLastChange("DEFERRED", to.name(), "user:alice", "because");
        }
    }

    @Test
    void row11AcceptedRejectedBackToOpenWithOptionalReason() throws Exception {
        detect(null);
        for (Status from : List.of(Status.ACCEPTED, Status.REJECTED)) {
            forceAccepted(from);
            triage.transition(id, Status.OPEN, from, null, "user:alice");
            assertEquals("OPEN", inc().get("status"));
            assertLastChange(from.name(), "OPEN", "user:alice", null);
        }
    }

    @Test
    void row12ResolvedBackToOpenForAnyVerificationResetsVerification() throws Exception {
        detect(null);
        for (String verification : List.of("pending", "confirmed")) {
            force(Status.RESOLVED, verification);
            triage.transition(id, Status.OPEN, Status.RESOLVED, null, "user:alice");
            assertEquals("OPEN", inc().get("status"));
            assertEquals("none", inc().get("verification"), verification);
            assertLastChange("RESOLVED", "OPEN", "user:alice", null);
        }
        force(Status.RESOLVED, "pending");
        assertEquals(
                List.of("OPEN"),
                triage.detail(id)
                        .get("allowedTransitions")
                        .valueStream()
                        .map(n -> n.asString())
                        .toList());
    }

    @Test
    void otherUserTransitionsAreRejectedWith422() throws Exception {
        detect(null);
        force(Status.RESOLVED, "pending");
        assertEquals(422, reject(Status.RESOLVED, Status.ACCEPTED, "x"));
        force(Status.OPEN, "none");
        assertEquals(422, reject(Status.OPEN, Status.OPEN, "x"));
        force(Status.ACCEPTED, "none");
        assertEquals(422, reject(Status.ACCEPTED, Status.RESOLVED, "x"));
        assertFalse(changes().isEmpty());
        assertNull(changes().getFirst().get("from"));
        assertTrue(changes().size() == 1);
    }

    int reject(Status from, Status to, String reason) {
        return assertThrows(
                        TriageService.TriageException.class,
                        () -> triage.transition(id, to, from, reason, "user:alice"))
                .status();
    }
}
