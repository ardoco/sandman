package edu.kit.kastel.mcse.ardoco.sandman.tlr.ardoco;

import edu.kit.kastel.mcse.ardoco.sandman.agent.Ids;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.TlrInput;
import java.text.Normalizer;
import java.util.Comparator;
import java.util.List;
import java.util.regex.Pattern;

/** One-sentence-per-line rendering of a document version (ADR-0015 S3) with the sentence-key hash assertion. */
final class SentenceRendering {

    private static final Pattern ASCII_WHITESPACE = Pattern.compile("[ \\t\\n\\r\\f\\u000B]+");
    private static final Pattern LINE_BREAKS = Pattern.compile("[\\r\\n\\u0085\\u2028\\u2029]+");

    private SentenceRendering() {}

    /** Sentence TextUnits ordered by sentenceIndex. */
    static List<TlrInput.Element> sentences(List<TlrInput.Element> elements, String versionId) {
        return elements.stream()
                .filter(e -> versionId.equals(e.versionId())
                        && "TextUnit".equals(e.label())
                        && "sentence".equals(e.kind())
                        && e.sentenceIndex() != null)
                .sorted(Comparator.comparing(TlrInput.Element::sentenceIndex))
                .toList();
    }

    /** Line texts in order; line breaks inside a sentence become one space, so that line i is sentence i. */
    static List<String> lines(List<TlrInput.Element> sentences) {
        return sentences.stream()
                .map(s -> LINE_BREAKS.matcher(s.text() == null ? "" : s.text()).replaceAll(" "))
                .toList();
    }

    /** Text handed to the annotation: each line ends with a newline. */
    static String text(List<String> lines) {
        return lines.stream().map(l -> l + "\n").reduce("", String::concat);
    }

    /**
     * Sentence key hash assertion: the hash of every raw sentence text (not the rendered line: line breaks inside the
     * sentence are replaced there, the key hash was taken before) equals the hash part of the sentence's localKey.
     */
    static void assertHashes(List<TlrInput.Element> sentences) {
        for (int i = 0; i < sentences.size(); i++) {
            String expected = hashPart(sentences.get(i).localKey());
            String actual =
                    hash(sentences.get(i).text() == null ? "" : sentences.get(i).text());
            if (!actual.equals(expected)) {
                throw new IllegalStateException(
                        "sentence " + i + " of version " + sentences.get(i).versionId() + " does not match its key "
                                + sentences.get(i).localKey() + " (text hash " + actual + ")");
            }
        }
    }

    static String normalize(String text) {
        return ASCII_WHITESPACE
                .matcher(Normalizer.normalize(text, Normalizer.Form.NFC))
                .replaceAll(" ")
                .replaceAll("^ | $", "");
    }

    static String hash(String text) {
        return Ids.sha256Hex(normalize(text).getBytes(java.nio.charset.StandardCharsets.UTF_8))
                .substring(0, 12);
    }

    /** The 12 hex characters after the last '/' of a sentence localKey, without the '~n' duplicate suffix. */
    static String hashPart(String localKey) {
        String last = localKey.substring(localKey.lastIndexOf('/') + 1);
        int tilde = last.indexOf('~');
        return tilde < 0 ? last : last.substring(0, tilde);
    }
}
