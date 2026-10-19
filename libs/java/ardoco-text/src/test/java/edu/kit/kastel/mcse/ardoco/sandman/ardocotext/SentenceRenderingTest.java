package edu.kit.kastel.mcse.ardoco.sandman.ardocotext;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import java.util.List;
import org.junit.jupiter.api.Test;

class SentenceRenderingTest {

    @Test
    void hashVectorsOfTheSentenceKey() {
        assertEquals("703c4e4a5dd6", SentenceRendering.hash("The  MediaCache stores\taudio files. "));
        assertEquals("703c4e4a5dd6", SentenceRendering.hash("The MediaCache stores audio files."));
        assertEquals("54b850c03299", SentenceRendering.hash("Größe und Café."));
        assertEquals("703c4e4a5dd6", SentenceRendering.hashPart("sec:Architecture/Cache/703c4e4a5dd6~3"));
    }

    @Test
    void lineReplacesLineBreaksAndTextEndsEveryLineWithNewline() {
        assertEquals(
                "The MediaCache stores audio files.", SentenceRendering.line("The MediaCache\nstores audio files."));
        assertEquals("a b", SentenceRendering.line("a\u2028b"));
        assertEquals("", SentenceRendering.line(null));
        assertEquals("a\nb\n", SentenceRendering.text(List.of("a", "b")));
    }

    @Test
    void hashMismatchFails() {
        var e = assertThrows(
                IllegalStateException.class,
                () -> SentenceRendering.assertHash(0, "v", "sec:/000000000000", "The MediaCache stores audio files."));
        assertEquals(true, e.getMessage().contains("703c4e4a5dd6"));
    }

    @Test
    void hashesTheRawTextEvenWhenTheRenderedLineDiffers() {
        String raw = "The MediaCache\u2028stores audio files.";
        SentenceRendering.assertHash(0, "v", "sec:/" + SentenceRendering.hash(raw), raw);
    }
}
