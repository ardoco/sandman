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

    /** Allowed parameter names; the skill rejects other names with INVALID_INPUT. Null = not restricted. */
    default Set<String> paramKeys() {
        return null;
    }

    /**
     * Identity of an external dependency whose answers change the links (for example the version of a provider
     * service). It enters configHash, so other dependencies get other link ids. Never return a placeholder for a dependency
     * that is down: throw {@link ApproachUnavailableException} (the skill then records no Run for this approach and
     * fails retryable). Null = none.
     */
    default String environment(TlrContext ctx) {
        return null;
    }

    /**
     * Called once when a skill call has run all its selected approaches (also after a failure or cancellation), so an
     * approach can drop caches that its approaches share within one call. Must not throw.
     */
    default void callFinished() {}

    /** Prompt template ids as 'name@hash' for Run.promptIds (they depend on the effective params); empty if none. */
    default List<String> promptIds(TlrContext ctx) {
        return List.of();
    }

    default ScopeMode scopeMode() {
        return ScopeMode.PAIRWISE;
    }
}
