package edu.kit.kastel.mcse.ardoco.sandman.tlr.ardoco;

import edu.kit.kastel.mcse.ardoco.sandman.tlr.TlrInput;
import java.util.Comparator;
import java.util.List;

/** TlrInput view of the shared rendering (libs/java/ardoco-text). */
final class SentenceRendering {

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

    static List<String> lines(List<TlrInput.Element> sentences) {
        return sentences.stream()
                .map(s -> edu.kit.kastel.mcse.ardoco.sandman.ardocotext.SentenceRendering.line(s.text()))
                .toList();
    }

    static String text(List<String> lines) {
        return edu.kit.kastel.mcse.ardoco.sandman.ardocotext.SentenceRendering.text(lines);
    }

    static void assertHashes(List<TlrInput.Element> sentences) {
        for (int i = 0; i < sentences.size(); i++) {
            var s = sentences.get(i);
            edu.kit.kastel.mcse.ardoco.sandman.ardocotext.SentenceRendering.assertHash(
                    i, s.versionId(), s.localKey(), s.text());
        }
    }
}
