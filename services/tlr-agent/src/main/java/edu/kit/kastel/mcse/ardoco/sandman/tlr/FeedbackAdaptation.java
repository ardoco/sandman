package edu.kit.kastel.mcse.ardoco.sandman.tlr;

import edu.kit.kastel.mcse.ardoco.sandman.agent.ArtifactStore;
import edu.kit.kastel.mcse.ardoco.sandman.agent.Ids;
import edu.kit.kastel.mcse.ardoco.sandman.agent.Jcs;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.OptionalDouble;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ArrayNode;
import tools.jackson.databind.node.ObjectNode;

/**
 * Feedback-driven adaptation (T8.2): snapshot hash, confidence threshold and few-shot selection from reviewed links.
 * Pure functions over review entries, so an offline experiment can feed simulated reviews.
 */
public final class FeedbackAdaptation {

    public static final int DEFAULT_FEW_SHOT = 2;
    public static final int DEFAULT_MIN_REVIEWS = 10;
    static final int SOURCE_CHARS = 500;
    static final int TARGET_CHARS = 1000;

    private FeedbackAdaptation() {}

    /** A direct review of one link; the element fields identify the pair, the texts feed the examples. */
    public record Entry(
            String approach,
            String status,
            double confidence,
            String reviewedAt,
            String reason,
            String sourceKey,
            String sourceHash,
            String targetKey,
            String targetHash,
            String sourceLabel,
            String targetLabel,
            String sourceText,
            String targetText,
            String targetStorageUri) {

        String pair() {
            return sourceKey + '\0' + sourceHash + '\0' + targetKey + '\0' + targetHash;
        }

        boolean confirmed() {
            return "confirmed".equals(status);
        }
    }

    /** The newest entry per approach and element pair, ordered by approach and keys. */
    public static List<Entry> latest(List<Entry> entries) {
        Map<String, Entry> best = new LinkedHashMap<>();
        for (Entry e : entries) {
            best.merge(
                    e.approach() + '\0' + e.pair(), e, (a, b) -> b.reviewedAt().compareTo(a.reviewedAt()) > 0 ? b : a);
        }
        return best.values().stream()
                .sorted(Comparator.comparing(Entry::approach)
                        .thenComparing(Entry::sourceKey)
                        .thenComparing(Entry::targetKey)
                        .thenComparing(Entry::sourceHash)
                        .thenComparing(Entry::targetHash))
                .toList();
    }

    /** SHA-256 of the JCS of the latest entries (identity, status, confidence, time, reason). */
    public static String snapshotHash(List<Entry> entries) {
        ArrayNode array = JsonMapper.shared().createArrayNode();
        for (Entry e : latest(entries)) {
            ObjectNode o = array.addObject();
            o.put("approach", e.approach());
            o.put("status", e.status());
            o.put("confidence", e.confidence());
            o.put("reviewedAt", e.reviewedAt());
            o.put("reason", e.reason() == null ? "" : e.reason());
            o.put("sourceKey", e.sourceKey());
            o.put("sourceHash", e.sourceHash());
            o.put("targetKey", e.targetKey());
            o.put("targetHash", e.targetHash());
        }
        return Ids.sha256Hex(Jcs.canonicalize(array));
    }

    /**
     * Threshold with the highest F1 over the reviewed links of the approach (lowest on a tie); empty if there are
     * fewer than minReviews pairs or one class is missing.
     */
    public static OptionalDouble threshold(List<Entry> entries, String approach, int minReviews) {
        List<Entry> mine = latest(entries).stream()
                .filter(e -> approach.equals(e.approach()))
                .toList();
        long positives = mine.stream().filter(Entry::confirmed).count();
        if (mine.size() < minReviews || positives == 0 || positives == mine.size()) {
            return OptionalDouble.empty();
        }
        double best = Double.NaN;
        double bestF1 = -1;
        for (double t :
                mine.stream().mapToDouble(Entry::confidence).distinct().sorted().toArray()) {
            long tp = mine.stream()
                    .filter(e -> e.confidence() >= t && e.confirmed())
                    .count();
            long predicted = mine.stream().filter(e -> e.confidence() >= t).count();
            double f1 = 2.0 * tp / (predicted + positives);
            if (f1 > bestF1) {
                bestF1 = f1;
                best = t;
            }
        }
        return OptionalDouble.of(best);
    }

    /** The newest n confirmed and n rejected element pairs over all approaches, newest first, then by keys. */
    public static List<Entry> select(List<Entry> entries, int n) {
        Map<String, Entry> pairs = new LinkedHashMap<>();
        for (Entry e : entries) {
            pairs.merge(e.pair(), e, (a, b) -> b.reviewedAt().compareTo(a.reviewedAt()) > 0 ? b : a);
        }
        Comparator<Entry> order = Comparator.comparing(Entry::reviewedAt)
                .reversed()
                .thenComparing(Entry::sourceKey)
                .thenComparing(Entry::targetKey);
        List<Entry> out = new ArrayList<>();
        for (boolean confirmed : new boolean[] {true, false}) {
            pairs.values().stream()
                    .filter(e -> e.confirmed() == confirmed)
                    .sorted(order)
                    .limit(n)
                    .forEach(out::add);
        }
        return out;
    }

    /** The examples with their texts; a pair whose target text cannot be read is skipped. */
    public static Feedback feedback(List<Entry> entries, int fewShot, ArtifactStore artifacts) {
        List<Feedback.Example> examples = new ArrayList<>();
        for (Entry e : select(entries, fewShot)) {
            String source = cut(e.sourceText(), SOURCE_CHARS);
            String target = e.targetText() != null ? cut(e.targetText(), TARGET_CHARS) : read(e, artifacts);
            if (source == null || target == null) {
                continue;
            }
            examples.add(
                    new Feedback.Example(e.sourceLabel(), source, e.targetLabel(), target, e.confirmed(), e.reason()));
        }
        return new Feedback(snapshotHash(entries), examples);
    }

    private static String read(Entry e, ArtifactStore artifacts) {
        if (artifacts == null || e.targetStorageUri() == null) {
            return null;
        }
        try {
            return cut(Files.readString(artifacts.pathFor(e.targetStorageUri()), StandardCharsets.UTF_8), TARGET_CHARS);
        } catch (IOException | RuntimeException ex) {
            return null;
        }
    }

    private static String cut(String text, int max) {
        if (text == null || text.isBlank()) {
            return null;
        }
        return text.length() > max ? text.substring(0, max) : text;
    }
}
