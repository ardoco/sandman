package edu.kit.kastel.mcse.ardoco.sandman.tlr;

import java.util.List;
import java.util.Set;

/** A trace link recovery approach, discovered via ServiceLoader and enabled in config/tlr/approaches.yaml. */
public interface TraceLinkApproach {

    String id();

    String version();

    Set<KindPair> supports();

    /** Candidate links; ctx.params() are the effective parameters (config merged with request overrides). */
    List<CandidateLink> recover(TlrInput in, TlrContext ctx);

    default boolean usesLlm() {
        return false;
    }
}
