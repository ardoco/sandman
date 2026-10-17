package edu.kit.kastel.mcse.ardoco.sandman.tlr.ardoco;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import edu.kit.kastel.mcse.ardoco.sandman.tlr.TlrInput;
import java.util.List;
import org.junit.jupiter.api.Test;

class SentenceRenderingTest {

    private static TlrInput.Element sentence(int index, String localKey, String text) {
        return new TlrInput.Element(
                "el_" + index,
                "ver_1",
                "art_1#" + localKey,
                localKey,
                "TextUnit",
                "sentence",
                text,
                text,
                index,
                "architecture-doc");
    }

    @Test
    void ordersBySentenceIndexAndKeepsDuplicatesAndWrappedSentences() {
        var a = sentence(1, "sec:/703c4e4a5dd6", "The MediaCache stores audio files.");
        var b = sentence(0, "sec:/54b850c03299", "Größe und Café.");
        var dup = sentence(2, "sec:/703c4e4a5dd6~2", "The MediaCache stores audio files.");
        var wrapped = sentence(3, "sec:/703c4e4a5dd6~3", "The MediaCache\nstores  audio files.");
        var noise = new TlrInput.Element(
                "el_x", "ver_1", "art_1#sec:", "sec:", "TextUnit", "section", "s", "t", null, "architecture-doc");
        var sentences = SentenceRendering.sentences(List.of(a, noise, dup, b, wrapped), "ver_1");

        assertEquals(List.of(b, a, dup, wrapped), sentences);
        var lines = SentenceRendering.lines(sentences);
        assertEquals(
                "Größe und Café.\nThe MediaCache stores audio files.\nThe MediaCache stores audio files.\n"
                        + "The MediaCache stores  audio files.\n",
                SentenceRendering.text(lines));
        SentenceRendering.assertHashes(sentences);
        assertEquals(List.of(), SentenceRendering.sentences(List.of(a), "ver_2"));
    }

    @Test
    void hashMismatchFailsTheRun() {
        var s = sentence(0, "sec:/000000000000", "The MediaCache stores audio files.");
        var e = assertThrows(IllegalStateException.class, () -> SentenceRendering.assertHashes(List.of(s)));
        assertEquals(true, e.getMessage().contains("703c4e4a5dd6"));
    }

    @Test
    void hashesTheRawTextEvenWhenTheRenderedLineDiffers() {
        String raw = "The MediaCache\u2028stores audio files.";
        var s = sentence(0, "sec:/" + edu.kit.kastel.mcse.ardoco.sandman.ardocotext.SentenceRendering.hash(raw), raw);

        assertEquals(List.of("The MediaCache stores audio files."), SentenceRendering.lines(List.of(s)));
        SentenceRendering.assertHashes(List.of(s));
    }
}
