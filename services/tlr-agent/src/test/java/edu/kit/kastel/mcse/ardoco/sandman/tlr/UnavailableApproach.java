package edu.kit.kastel.mcse.ardoco.sandman.tlr;

import java.util.List;
import java.util.Set;

/** Test-only approach whose dependency is down; never selected by default, only by name. */
public final class UnavailableApproach implements TraceLinkApproach {

    @Override
    public String id() {
        return "test-unavailable";
    }

    @Override
    public String version() {
        return "1";
    }

    @Override
    public Set<KindPair> supports() {
        return Set.of(new KindPair("test", "other"));
    }

    @Override
    public String environment(TlrContext ctx) {
        return "dependency=down";
    }

    @Override
    public List<CandidateLink> recover(TlrInput in, TlrContext ctx) {
        throw new ApproachUnavailableException("dependency down", new java.io.IOException("refused"));
    }
}
