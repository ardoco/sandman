package edu.kit.kastel.mcse.ardoco.sandman.tlr;

import static org.junit.jupiter.api.Assertions.assertEquals;

import java.util.List;
import java.util.Set;
import org.junit.jupiter.api.Test;
import org.slf4j.LoggerFactory;
import tools.jackson.databind.json.JsonMapper;

class StubNameMatchTest {

    @Test
    void splitsCamelCaseAndPunctuationAndDropsShortTokens() {
        assertEquals(Set.of("cache", "media", "files"), StubNameMatch.tokens("Cache media files", 4));
        assertEquals(Set.of("media", "cache", "java"), StubNameMatch.tokens("MediaCache.java", 4));
        assertEquals(Set.of("user"), StubNameMatch.tokens("UserDB", 4));
        assertEquals(Set.of("http", "server"), StubNameMatch.tokens("HTTPServer", 4));
    }

    private static TlrInput.Element element(String id, String name) {
        return new TlrInput.Element(id, "v", "a#" + id, id, "CodeUnit", "type", name, null, null, "code");
    }

    private static List<CandidateLink> run(TlrInput in) {
        return new StubNameMatch()
                .recover(
                        in,
                        new TlrContext(
                                null,
                                null,
                                () -> false,
                                LoggerFactory.getLogger("t"),
                                JsonMapper.shared().createObjectNode()));
    }

    @Test
    void identifiesItselfAndSupportsEveryKindPair() {
        var approach = new StubNameMatch();
        assertEquals("stub-name-match", approach.id());
        assertEquals("1", approach.version());
        assertEquals(KindPair.all(), approach.supports());
    }

    @Test
    void linksElementsThatShareTokensWithDefaultsAndSkipsTheSameElement() {
        var in = new TlrInput(
                "p",
                List.of(),
                List.of(element("s1", "MediaCache"), element("s2", "Other")),
                List.of(element("s1", "MediaCache"), element("t1", "cache_store media"), element("t2", "Unrelated")));
        var links = run(in);
        assertEquals(1, links.size());
        assertEquals("s1", links.get(0).sourceId());
        assertEquals("t1", links.get(0).targetId());
        assertEquals("relates", links.get(0).linkType());
        assertEquals(0.5, links.get(0).confidence());
        assertEquals("shared tokens: cache, media", links.get(0).rationale());
        assertEquals(
                List.of("t1"),
                links.stream().map(CandidateLink::targetId).distinct().toList());
    }

    @Test
    void sourceWithoutNameOrWithOnlyShortTokensHasNoLinks() {
        var in = new TlrInput(
                "p", List.of(), List.of(element("s", null), element("s2", "DB")), List.of(element("t", "DB Cache")));
        assertEquals(List.of(), run(in));
    }
}
