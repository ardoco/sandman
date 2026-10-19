package edu.kit.kastel.mcse.ardoco.sandman.inconsistency.ardoco;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;

import edu.kit.kastel.mcse.ardoco.core.api.models.ModelFormat;
import edu.kit.kastel.mcse.ardoco.sandman.inconsistency.ardoco.KgInput.Version;
import org.junit.jupiter.api.Test;

class ArdocoIdDetectorTest {

    @Test
    void formatFollowsTheArtifactFormatThenTheSuffix() {
        assertEquals(
                ModelFormat.PCM, ArdocoIdDetector.format(new Version("v", "a/ms.repository", "pcm-repository", null)));
        assertEquals(ModelFormat.UML, ArdocoIdDetector.format(new Version("v", "a/ms.x", "uml", null)));
        assertEquals(ModelFormat.PCM, ArdocoIdDetector.format(new Version("v", "a/ms.repository", null, null)));
        assertEquals(ModelFormat.UML, ArdocoIdDetector.format(new Version("v", "a/MS.XMI", null, null)));
        assertEquals(ModelFormat.UML, ArdocoIdDetector.format(new Version("v", "a/ms.uml", null, null)));
        assertEquals(
                ModelFormat.COMPONENT_LISTING,
                ArdocoIdDetector.format(new Version("v", "a/x.components", "component-listing", null)));
        assertEquals(
                ModelFormat.COMPONENT_LISTING, ArdocoIdDetector.format(new Version("v", "a/X.components", null, null)));
        assertNull(ArdocoIdDetector.format(new Version("v", "a/ms.json", "acm", null)));
        assertNull(ArdocoIdDetector.format(new Version("v", "a/ms.json", null, null)));
    }

    @Test
    void declaresTheStage2Contract() {
        var d = new ArdocoIdDetector();
        assertEquals("ardoco-id", d.id());
        assertEquals(java.util.Set.of("textKinds", "modelKinds", "linkApproaches"), d.paramKeys());
        assertEquals(java.util.Set.of("undocumented-model-element", "missing-model-element"), d.types());
        assertEquals(
                edu.kit.kastel.mcse.ardoco.sandman.inconsistency.InconsistencyDetector.ScopeMode.WHOLE_PROJECT,
                d.scopeMode());
        assertEquals(false, d.usesLlm());
    }
}
