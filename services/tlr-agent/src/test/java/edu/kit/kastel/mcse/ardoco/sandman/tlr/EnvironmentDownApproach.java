package edu.kit.kastel.mcse.ardoco.sandman.tlr;

import java.util.List;
import java.util.Set;

/** Test-only approach whose environment (not its recover) reports the dependency as down; selected only by name. */
public final class EnvironmentDownApproach implements TraceLinkApproach {

    @Override
    public String id() {
        return "test-environment-down";
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
        throw new ApproachUnavailableException("environment down", null);
    }

    @Override
    public List<CandidateLink> recover(TlrInput in, TlrContext ctx) {
        throw new AssertionError("recover must not run when the environment is unavailable");
    }
}
