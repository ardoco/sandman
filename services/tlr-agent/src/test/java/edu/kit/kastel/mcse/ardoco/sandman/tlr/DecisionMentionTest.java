package edu.kit.kastel.mcse.ardoco.sandman.tlr;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.util.List;
import org.junit.jupiter.api.Test;
import org.slf4j.LoggerFactory;
import tools.jackson.databind.json.JsonMapper;

class DecisionMentionTest {

    private static final String PATH = "src/main/java/mini/MediaCache.java";

    private static TlrInput.Element element(String id, String label, String kind, String localKey, String name) {
        return new TlrInput.Element(id, "v", "a#" + localKey, localKey, label, kind, name, null, null, "code");
    }

    private static TlrInput.Element decision(String text) {
        return new TlrInput.Element(
                "d", "v", "a#file", "file", "Decision", "decision", "Use caching", text, null, "architecture-doc");
    }

    private static List<CandidateLink> run(String text, int minNameLength, TlrInput.Element... targets) {
        var params = JsonMapper.shared().createObjectNode().put("minNameLength", minNameLength);
        var in = new TlrInput("p", List.of(), List.of(decision(text)), List.of(targets));
        return new DecisionMention()
                .recover(in, new TlrContext(null, null, () -> false, LoggerFactory.getLogger("t"), params));
    }

    private static TlrInput.Element file() {
        return element("f", "CodeUnit", "file", PATH, "MediaCache.java");
    }

    private static TlrInput.Element component(String name) {
        return element("c", "ModelElement", "component", "id-1", name);
    }

    @Test
    void pathMatchHasConfidenceOne() {
        var links = run("We change " + PATH + " now.", 4, file());
        assertEquals(1, links.size());
        var link = links.getFirst();
        assertEquals(("d"), link.sourceId());
        assertEquals(("f"), link.targetId());
        assertEquals("relates", link.linkType());
        assertEquals(1.0, link.confidence());
        assertEquals("mentions path '" + PATH + "'", link.rationale());
    }

    @Test
    void wholeWordNameMatchHasConfidenceEightTenths() {
        var links = run("The MediaCache component caches.", 4, component("MediaCache"));
        assertEquals(1, links.size());
        assertEquals(0.8, links.getFirst().confidence());
        assertEquals("mentions 'MediaCache'", links.getFirst().rationale());
    }

    @Test
    void noMatchInsideLongerWord() {
        assertTrue(run("The MediaCacheManager and xMediaCache differ.", 4, component("MediaCache"))
                .isEmpty());
    }

    @Test
    void matchIsCaseSensitive() {
        assertTrue(run("The mediacache is small.", 4, component("MediaCache")).isEmpty());
    }

    @Test
    void shortNamesAreSkipped() {
        assertTrue(run("The DB stores data.", 4, component("DB")).isEmpty());
        assertEquals(1, run("The DB stores data.", 2, component("DB")).size());
    }

    @Test
    void onlyDecisionSourcesAndMentionTargets() {
        var sentence = new TlrInput.Element(
                "s", "v", "a#x", "x", "TextUnit", "sentence", "s", "MediaCache", 0, "architecture-doc");
        var method = element("m", "CodeUnit", "method", "m", "MediaCache");
        var in = new TlrInput("p", List.of(), List.of(sentence), List.of(component("MediaCache")));
        var ctx = new TlrContext(
                null,
                null,
                () -> false,
                LoggerFactory.getLogger("t"),
                JsonMapper.shared().createObjectNode());
        assertTrue(new DecisionMention().recover(in, ctx).isEmpty());
        assertTrue(run("MediaCache", 4, method).isEmpty());
    }

    @Test
    void stubFileKeyIsNotTreatedAsPath() {
        assertTrue(run("Read the file first.", 4, element("f", "CodeUnit", "file", "file", "Other.java"))
                .isEmpty());
    }
}
