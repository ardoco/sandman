package edu.kit.kastel.mcse.ardoco.sandman.inconsistency.ardoco;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import edu.kit.kastel.mcse.ardoco.sandman.inconsistency.Finding;
import edu.kit.kastel.mcse.ardoco.sandman.inconsistency.ardoco.Mapping.ModelEl;
import edu.kit.kastel.mcse.ardoco.sandman.inconsistency.ardoco.Mapping.ModelHit;
import edu.kit.kastel.mcse.ardoco.sandman.inconsistency.ardoco.Mapping.Raw;
import edu.kit.kastel.mcse.ardoco.sandman.inconsistency.ardoco.Mapping.RunResult;
import edu.kit.kastel.mcse.ardoco.sandman.inconsistency.ardoco.Mapping.Sentence;
import edu.kit.kastel.mcse.ardoco.sandman.inconsistency.ardoco.Mapping.WordHit;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.Test;

class MappingTest {

    private static final List<Sentence> SENTENCES =
            List.of(new Sentence("doc#sec:/aaa"), new Sentence("doc#sec:/bbb"), new Sentence("doc#sec:/ccc"));
    private static final Map<String, ModelEl> MODEL = Map.of(
            "id-cache", new ModelEl("model#id-cache", "Cache"),
            "id-iface", new ModelEl("model#id-iface", "IDownload"),
            "id-web", new ModelEl("model#id-web", "WebGUI"));

    private static RunResult run(String doc, String model, Raw raw) {
        return new RunResult(doc, model, SENTENCES, MODEL, raw);
    }

    private static List<Finding> ofType(List<Finding> all, String type) {
        return all.stream().filter(f -> f.type().equals(type)).toList();
    }

    @Test
    void modelFindingsIncludingAnInterfaceAreUndocumentedWarnings() {
        var raw = new Raw(
                List.of(
                        new ModelHit("id-cache", "Cache", "Component"),
                        new ModelHit("id-iface", "IDownload", "Interface")),
                List.of());
        var findings = Mapping.map(List.of(run("d1", "m1", raw)));
        assertEquals(2, findings.size());
        Finding f = findings.get(0);
        assertEquals("undocumented-model-element", f.type());
        assertEquals("warning", f.severity());
        assertEquals("model#id-cache", f.involved().get(0).elementKey());
        assertEquals("subject", f.involved().get(0).role());
        assertEquals("id-cache", f.evidence().get("modelId"));
        assertEquals("model#id-iface", findings.get(1).involved().get(0).elementKey());
    }

    @Test
    void unknownModelIdFails() {
        var raw = new Raw(List.of(new ModelHit("nope", "X", "Component")), List.of());
        var e = assertThrows(IllegalStateException.class, () -> Mapping.map(List.of(run("d1", "m1", raw))));
        assertTrue(e.getMessage().contains("unknown model id nope"));
    }

    @Test
    void duplicateWordKeepsTheMaximumConfidenceAndWordsMergePerSentence() {
        var raw = new Raw(
                List.of(),
                List.of(
                        new WordHit(1, "Media", 0.63),
                        new WordHit(1, "Media", 0.65),
                        new WordHit(1, "Aaa", 0.5),
                        new WordHit(3, "Data", 0.4)));
        var findings = Mapping.map(List.of(run("d1", "m1", raw)));
        assertEquals(2, findings.size());
        Finding s1 = findings.get(0);
        assertEquals("missing-model-element", s1.type());
        assertEquals("info", s1.severity());
        assertEquals("doc#sec:/aaa", s1.involved().get(0).elementKey());
        assertEquals(0.65, s1.confidence(), 1e-9);
        assertEquals(
                List.of(Map.of("word", "Aaa", "confidence", 0.5), Map.of("word", "Media", "confidence", 0.65)),
                s1.evidence().get("words"));
        assertEquals("doc#sec:/ccc", findings.get(1).involved().get(0).elementKey());
    }

    @Test
    void unknownSentenceFails() {
        var raw = new Raw(List.of(), List.of(new WordHit(4, "X", 1.0)));
        assertThrows(IllegalStateException.class, () -> Mapping.map(List.of(run("d1", "m1", raw))));
    }

    @Test
    void modelFindingNeedsEveryRunOfThatModel() {
        var cache = new ModelHit("id-cache", "Cache", "Component");
        var web = new ModelHit("id-web", "WebGUI", "Component");
        var r1 = run("d1", "m1", new Raw(List.of(cache, web), List.of()));
        var r2 = run("d2", "m1", new Raw(List.of(cache), List.of()));
        var findings = ofType(Mapping.map(List.of(r1, r2)), "undocumented-model-element");
        assertEquals(1, findings.size());
        assertEquals("id-cache", findings.get(0).evidence().get("modelId"));
        assertEquals(List.of("d1", "d2"), findings.get(0).evidence().get("docVersionIds"));
    }

    @Test
    void textFindingNeedsEveryRunOfThatDoc() {
        var r1 = run("d1", "m1", new Raw(List.of(), List.of(new WordHit(1, "A", 0.5), new WordHit(2, "B", 0.6))));
        var r2 = run("d1", "m2", new Raw(List.of(), List.of(new WordHit(1, "A", 0.7))));
        var findings = ofType(Mapping.map(List.of(r1, r2)), "missing-model-element");
        assertEquals(1, findings.size());
        assertEquals("doc#sec:/aaa", findings.get(0).involved().get(0).elementKey());
        assertEquals(0.7, findings.get(0).confidence(), 1e-9);
    }

    @Test
    void noRunsNoFindings() {
        assertEquals(List.of(), Mapping.map(List.of()));
    }

    @Test
    void inferredModelElementCapsSeverityAndFlagsEvidence() {
        var inferred = Map.of(
                "id-cache", new ModelEl("model#id-cache", "Cache", true),
                "id-web", new ModelEl("model#id-web", "WebGUI", false));
        var raw = new Raw(
                List.of(new ModelHit("id-cache", "Cache", "Component"), new ModelHit("id-web", "WebGUI", "Component")),
                List.of());
        var findings = Mapping.map(List.of(new RunResult("d1", "m1", SENTENCES, inferred, raw)));
        assertEquals("info", findings.get(0).severity());
        assertEquals(true, findings.get(0).evidence().get("inferredModel"));
        assertEquals("warning", findings.get(1).severity());
        assertTrue(!findings.get(1).evidence().containsKey("inferredModel"));
    }

    @Test
    void textFindingAgainstInferredModelIsFlagged() {
        var inferred = Map.of("id-cache", new ModelEl("model#id-cache", "Cache", true));
        var raw = new Raw(List.of(), List.of(new WordHit(1, "Foo", 0.5)));
        var findings = Mapping.map(List.of(new RunResult("d1", "m1", SENTENCES, inferred, raw)));
        assertEquals("info", findings.get(0).severity());
        assertEquals(true, findings.get(0).evidence().get("inferredModel"));
    }
}
