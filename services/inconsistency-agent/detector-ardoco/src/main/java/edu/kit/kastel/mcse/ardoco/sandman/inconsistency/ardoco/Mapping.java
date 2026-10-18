package edu.kit.kastel.mcse.ardoco.sandman.inconsistency.ardoco;

import edu.kit.kastel.mcse.ardoco.sandman.inconsistency.Finding;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;
import java.util.TreeSet;

/** Maps raw ArDoCo results of all (doc, model) runs to findings (decisions 1, 4 and 5 of T3.1). */
final class Mapping {

    static final String UNDOCUMENTED = "undocumented-model-element";
    static final String MISSING = "missing-model-element";

    /** ModelEntityAbsentFromText: ArDoCo model id, element name and ArDoCo element type. */
    record ModelHit(String modelId, String name, String type) {}

    /** TextEntityAbsentFromModel: 1-based sentence number, word and confidence. */
    record WordHit(int sentence, String word, double confidence) {}

    record Raw(List<ModelHit> models, List<WordHit> words) {}

    /** A sentence TextUnit of the doc version; list index = line index = sentenceIndex (0-based). */
    record Sentence(String key) {}

    /** A ModelElement of the model version. */
    record ModelEl(String key, String name, boolean inferred) {
        ModelEl(String key, String name) {
            this(key, name, false);
        }
    }

    /** One finished run: its input and its raw result. */
    record RunResult(
            String docVersionId,
            String modelVersionId,
            List<Sentence> sentences,
            Map<String, ModelEl> modelByLocalKey,
            Raw raw) {}

    private Mapping() {}

    static List<Finding> map(List<RunResult> runs) {
        List<Finding> out = new ArrayList<>();
        out.addAll(modelFindings(runs));
        out.addAll(textFindings(runs));
        return out;
    }

    private static List<Finding> modelFindings(List<RunResult> runs) {
        // key: model version id; element reported if every run with that model reports it
        Map<String, List<RunResult>> byModel = new TreeMap<>();
        runs.forEach(r -> byModel.computeIfAbsent(r.modelVersionId(), k -> new ArrayList<>())
                .add(r));
        List<Finding> out = new ArrayList<>();
        for (var entry : byModel.entrySet()) {
            List<RunResult> modelRuns = entry.getValue();
            Map<String, ModelHit> common = null;
            for (RunResult run : modelRuns) {
                Map<String, ModelHit> hits = new TreeMap<>();
                for (ModelHit hit : run.raw().models()) {
                    if (!run.modelByLocalKey().containsKey(hit.modelId())) {
                        throw new IllegalStateException("ArDoCo reported unknown model id " + hit.modelId()
                                + " (model version " + run.modelVersionId() + ")");
                    }
                    hits.putIfAbsent(hit.modelId(), hit);
                }
                if (common == null) {
                    common = hits;
                } else {
                    common.keySet().retainAll(hits.keySet());
                }
            }
            Set<String> docs = new TreeSet<>();
            modelRuns.forEach(r -> docs.add(r.docVersionId()));
            for (ModelHit hit : common.values()) {
                ModelEl el = modelRuns.get(0).modelByLocalKey().get(hit.modelId());
                Map<String, Object> evidence = new LinkedHashMap<>();
                evidence.put("modelId", hit.modelId());
                evidence.put("elementName", hit.name());
                evidence.put("elementType", hit.type());
                evidence.put("docVersionIds", List.copyOf(docs));
                if (el.inferred()) {
                    evidence.put("inferredModel", true);
                }
                out.add(new Finding(
                        UNDOCUMENTED,
                        el.inferred() ? "info" : "warning",
                        cut("Undocumented model element: " + hit.name()),
                        "The architecture model contains " + hit.type() + " \"" + hit.name()
                                + "\" but the documentation does not seem to mention it.",
                        null,
                        List.of(Finding.Involved.element(el.key(), "subject")),
                        evidence));
            }
        }
        return out;
    }

    private static List<Finding> textFindings(List<RunResult> runs) {
        Map<String, List<RunResult>> byDoc = new TreeMap<>();
        runs.forEach(r ->
                byDoc.computeIfAbsent(r.docVersionId(), k -> new ArrayList<>()).add(r));
        List<Finding> out = new ArrayList<>();
        for (var entry : byDoc.entrySet()) {
            List<RunResult> docRuns = entry.getValue();
            Map<Integer, Map<String, Double>> common = null;
            for (RunResult run : docRuns) {
                Map<Integer, Map<String, Double>> perSentence = new TreeMap<>();
                for (WordHit hit : run.raw().words()) {
                    int index = hit.sentence() - 1;
                    if (index < 0 || index >= run.sentences().size()) {
                        throw new IllegalStateException("ArDoCo reported unknown sentence " + hit.sentence()
                                + " (document version " + run.docVersionId() + ")");
                    }
                    perSentence
                            .computeIfAbsent(index, k -> new TreeMap<>())
                            .merge(hit.word(), hit.confidence(), Math::max);
                }
                if (common == null) {
                    common = perSentence;
                } else {
                    for (var it = common.entrySet().iterator(); it.hasNext(); ) {
                        var e = it.next();
                        Map<String, Double> other = perSentence.get(e.getKey());
                        if (other == null) {
                            it.remove();
                        } else {
                            other.forEach((w, c) -> e.getValue().merge(w, c, Math::max));
                        }
                    }
                }
            }
            for (var e : common.entrySet()) {
                Sentence sentence = docRuns.get(0).sentences().get(e.getKey());
                List<Map<String, Object>> words = new ArrayList<>();
                e.getValue().forEach((w, c) -> words.add(Map.of("word", w, "confidence", c)));
                words.sort(Comparator.<Map<String, Object>, String>comparing(m -> (String) m.get("word"))
                        .thenComparing(m -> (Double) m.get("confidence")));
                double max = e.getValue().values().stream()
                        .mapToDouble(Double::doubleValue)
                        .max()
                        .orElse(0);
                Map<String, Object> evidence = new LinkedHashMap<>();
                evidence.put("docVersionId", entry.getKey());
                evidence.put("sentence", e.getKey() + 1);
                evidence.put("words", words);
                if (docRuns.stream()
                        .anyMatch(r -> r.modelByLocalKey().values().stream().anyMatch(ModelEl::inferred))) {
                    evidence.put("inferredModel", true);
                }
                out.add(new Finding(
                        MISSING,
                        "info",
                        cut("Sentence mentions elements missing from the model: "
                                + String.join(", ", e.getValue().keySet())),
                        "The text suggests that these words denote architecture elements that the model does not contain.",
                        max,
                        List.of(Finding.Involved.element(sentence.key(), "subject")),
                        evidence));
            }
        }
        return out;
    }

    private static String cut(String title) {
        return title.substring(0, Math.min(200, title.length()));
    }
}
