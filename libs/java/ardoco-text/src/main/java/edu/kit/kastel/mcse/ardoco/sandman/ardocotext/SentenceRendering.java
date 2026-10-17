package edu.kit.kastel.mcse.ardoco.sandman.ardocotext;

import edu.kit.kastel.mcse.ardoco.sandman.agent.Ids;
import java.nio.charset.StandardCharsets;
import java.text.Normalizer;
import java.util.List;
import java.util.regex.Pattern;

/** One-sentence-per-line rendering of a document version (ADR-0015 S3) with the sentence-key hash assertion. */
public final class SentenceRendering {

    private static final Pattern ASCII_WHITESPACE = Pattern.compile("[ \\t\\n\\r\\f\\u000B]+");
    private static final Pattern LINE_BREAKS = Pattern.compile("[\\r\\n\\u0085\\u2028\\u2029]+");

    private SentenceRendering() {}

    /** Line text of one sentence: line breaks inside the sentence become one space, so that line i is sentence i. */
    public static String line(String sentenceText) {
        return LINE_BREAKS.matcher(sentenceText == null ? "" : sentenceText).replaceAll(" ");
    }

    /** Text handed to the annotation: each line ends with a newline. */
    public static String text(List<String> lines) {
        return lines.stream().map(l -> l + "\n").reduce("", String::concat);
    }

    /**
     * Sentence key hash assertion for one sentence: the hash of the raw sentence text (not the rendered line) equals
     * the hash part of its localKey.
     */
    public static void assertHash(int index, String versionId, String localKey, String rawText) {
        String actual = hash(rawText == null ? "" : rawText);
        if (!actual.equals(hashPart(localKey))) {
            throw new IllegalStateException("sentence " + index + " of version " + versionId
                    + " does not match its key " + localKey + " (text hash " + actual + ")");
        }
    }

    public static String normalize(String text) {
        return ASCII_WHITESPACE
                .matcher(Normalizer.normalize(text, Normalizer.Form.NFC))
                .replaceAll(" ")
                .replaceAll("^ | $", "");
    }

    public static String hash(String text) {
        return Ids.sha256Hex(normalize(text).getBytes(StandardCharsets.UTF_8)).substring(0, 12);
    }

    /** The 12 hex characters after the last '/' of a sentence localKey, without the '~n' duplicate suffix. */
    public static String hashPart(String localKey) {
        String last = localKey.substring(localKey.lastIndexOf('/') + 1);
        int tilde = last.indexOf('~');
        return tilde < 0 ? last : last.substring(0, tilde);
    }
}
