package edu.kit.kastel.mcse.ardoco.sandman.tlr;

import static org.junit.jupiter.api.Assertions.assertEquals;

import java.util.Set;
import org.junit.jupiter.api.Test;

class StubNameMatchTest {

    @Test
    void splitsCamelCaseAndPunctuationAndDropsShortTokens() {
        assertEquals(Set.of("cache", "media", "files"), StubNameMatch.tokens("Cache media files", 4));
        assertEquals(Set.of("media", "cache", "java"), StubNameMatch.tokens("MediaCache.java", 4));
        assertEquals(Set.of("user"), StubNameMatch.tokens("UserDB", 4));
        assertEquals(Set.of("http", "server"), StubNameMatch.tokens("HTTPServer", 4));
    }
}
