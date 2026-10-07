package edu.kit.kastel.mcse.ardoco.sandman.tlr;

import java.util.List;
import java.util.Set;

/** Test-only approach that always fails. */
public final class FailingApproach implements TraceLinkApproach {

    @Override
    public String id() {
        return "test-failing";
    }

    @Override
    public String version() {
        return "1";
    }

    @Override
    public Set<KindPair> supports() {
        return KindPair.all();
    }

    @Override
    public List<CandidateLink> recover(TlrInput in, TlrContext ctx) {
        throw new IllegalStateException("boom");
    }
}
