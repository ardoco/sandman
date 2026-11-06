package edu.kit.kastel.mcse.ardoco.sandman.tlr;

import static org.junit.jupiter.api.Assertions.assertEquals;

import java.util.List;
import org.junit.jupiter.api.Test;

class GroundingTest {

    private static TlrInput.Element element(String id) {
        return new TlrInput.Element(id, "v", "a#" + id, id, "Element", null, id, "", null, "requirements");
    }

    private static CandidateLink link(String source, String target) {
        return new CandidateLink(source, target, "relates", 0.9, null);
    }

    @Test
    void rejectsCandidatesWithIdsOutsideTheInput() {
        var in = new TlrInput("p", List.of(), List.of(element("s1")), List.of(element("t1")));
        var good = link("s1", "t1");

        var kept = RecoverTraceLinks.grounded(
                in, List.of(good, link("s1", "invented"), link("invented", "t1"), link("t1", "s1")));

        assertEquals(List.of(good), kept);
    }
}
