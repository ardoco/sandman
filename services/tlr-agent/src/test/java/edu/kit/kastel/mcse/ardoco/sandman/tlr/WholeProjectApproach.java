package edu.kit.kastel.mcse.ardoco.sandman.tlr;

import java.util.ArrayList;
import java.util.List;
import java.util.Set;

/** Test-only whole-project approach: links a document element to a model element whose name its text contains. */
public final class WholeProjectApproach implements TraceLinkApproach {

    @Override
    public String id() {
        return "test-whole";
    }

    @Override
    public String version() {
        return "1";
    }

    @Override
    public Set<KindPair> supports() {
        return Set.of(new KindPair("architecture-doc", "architecture-model"));
    }

    @Override
    public ScopeMode scopeMode() {
        return ScopeMode.WHOLE_PROJECT;
    }

    @Override
    public List<CandidateLink> recover(TlrInput in, TlrContext ctx) {
        List<CandidateLink> links = new ArrayList<>();
        for (var s : in.sources()) {
            for (var t : in.targets()) {
                if (s.text() != null && s.text().contains(t.name())) {
                    links.add(new CandidateLink(s.id(), t.id(), "documents", 1.0, null));
                }
            }
        }
        return links;
    }
}
