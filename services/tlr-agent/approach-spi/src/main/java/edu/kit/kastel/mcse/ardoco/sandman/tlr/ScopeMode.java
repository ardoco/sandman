package edu.kit.kastel.mcse.ardoco.sandman.tlr;

/** How an approach selects its input (contracts ListApproachesOutput.scopeMode, R-022). */
public enum ScopeMode {
    /** Links the requested source and target versions only. */
    PAIRWISE("pairwise"),
    /** Ignores the requested versions; runs over all current versions of the supported kinds and retires its other links. */
    WHOLE_PROJECT("whole-project");

    private final String wire;

    ScopeMode(String wire) {
        this.wire = wire;
    }

    public String wire() {
        return wire;
    }
}
