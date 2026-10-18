package edu.kit.kastel.mcse.ardoco.sandman.tlr.ardoco;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import edu.kit.kastel.mcse.ardoco.core.api.models.ModelFormat;
import edu.kit.kastel.mcse.ardoco.sandman.agent.ArtifactStore;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.CandidateLink;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.KindPair;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.ScopeMode;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.TlrContext;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.TlrInput;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Map;
import java.util.Set;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.slf4j.LoggerFactory;
import tools.jackson.databind.json.JsonMapper;

class TransarcApproachTest {

    private static final TlrInput.Version MODEL =
            new TlrInput.Version("v", "a", "m.components", "architecture-model", "component-listing", null);

    private static TlrInput.Element element(String id, String key) {
        return new TlrInput.Element(id, "v", "a#" + key, key, "Component", "component", key, null, null, "x");
    }

    @Test
    void declaresTheWholeProjectPairs() {
        var approach = new TransarcApproach();
        assertEquals("transarc", approach.id());
        assertEquals(ScopeMode.WHOLE_PROJECT, approach.scopeMode());
        assertEquals(
                Set.of(new KindPair("architecture-doc", "code"), new KindPair("architecture-model", "code")),
                approach.supports());
        assertEquals(Set.of(), approach.paramKeys());
    }

    @Test
    void mapsSamCodePairsToDistinctSortedImplementsLinks() {
        Map<String, TlrInput.Element> models = Map.of("A", element("ea", "A"), "B", element("eb", "B"));
        Map<String, TlrInput.Element> files = Map.of("f1", element("f1id", "f1"), "f2", element("f2id", "f2"));
        List<CandidateLink> links = TransarcApproach.samToLinks(
                List.of(Map.entry("B", "f1"), Map.entry("A", "f2"), Map.entry("B", "f1")), models, files, MODEL);
        assertEquals(
                List.of(
                        new CandidateLink("ea", "f2id", "implements", 1.0, null),
                        new CandidateLink("eb", "f1id", "implements", 1.0, null)),
                links);
    }

    @Test
    void unknownModelIdOrFileFailsTheRun() {
        Map<String, TlrInput.Element> models = Map.of("A", element("ea", "A"));
        Map<String, TlrInput.Element> files = Map.of("f1", element("f1id", "f1"));
        assertThrows(
                IllegalStateException.class,
                () -> TransarcApproach.samToLinks(List.of(Map.entry("Z", "f1")), models, files, MODEL));
        assertThrows(
                IllegalStateException.class,
                () -> TransarcApproach.samToLinks(List.of(Map.entry("A", "nope")), models, files, MODEL));
    }

    @Test
    void componentListingIsAModelFormat() {
        assertEquals(ModelFormat.COMPONENT_LISTING, ArdocoApproach.format(MODEL));
        assertEquals(
                ModelFormat.COMPONENT_LISTING,
                ArdocoApproach.format(
                        new TlrInput.Version("v", "a", "x/M.Components", "architecture-model", null, null)));
    }

    @Test
    void stagesTheModelFileUnderItsName(@TempDir Path tmp) throws Exception {
        var store = new ArtifactStore(tmp.resolve("store"));
        var stored = store.put("mediastore", "A::A\n".getBytes(StandardCharsets.UTF_8));
        var version = new TlrInput.Version(
                "v", "a", "dir/m.components", "architecture-model", "component-listing", stored.uri());
        var ctx = new TlrContext(
                null,
                store,
                () -> false,
                LoggerFactory.getLogger(TransarcApproach.class),
                JsonMapper.shared().createObjectNode());
        Path dir = Files.createDirectories(tmp.resolve("run"));
        Path staged = ArdocoRuns.stageModel(ctx, dir, version);
        assertEquals(dir.resolve("m.components"), staged);
        assertEquals("A::A\n", Files.readString(staged));
    }
}
