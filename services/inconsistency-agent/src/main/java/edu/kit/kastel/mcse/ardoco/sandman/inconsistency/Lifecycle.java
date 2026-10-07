package edu.kit.kastel.mcse.ardoco.sandman.inconsistency;

import static edu.kit.kastel.mcse.ardoco.sandman.inconsistency.Lifecycle.Status.ACCEPTED;
import static edu.kit.kastel.mcse.ardoco.sandman.inconsistency.Lifecycle.Status.DEFERRED;
import static edu.kit.kastel.mcse.ardoco.sandman.inconsistency.Lifecycle.Status.OPEN;
import static edu.kit.kastel.mcse.ardoco.sandman.inconsistency.Lifecycle.Status.REJECTED;
import static edu.kit.kastel.mcse.ardoco.sandman.inconsistency.Lifecycle.Status.REOPENED;
import static edu.kit.kastel.mcse.ardoco.sandman.inconsistency.Lifecycle.Status.RESOLVED;

import java.util.EnumSet;
import java.util.Set;

/** The inconsistency state machine (ADR-0007, Stage 1 §4.3); pure, no I/O. */
public final class Lifecycle {

    public enum Status {
        OPEN,
        DEFERRED,
        ACCEPTED,
        REJECTED,
        RESOLVED,
        REOPENED
    }

    private Lifecycle() {}

    /** Targets a user may choose from a status. */
    public static Set<Status> userTargets(Status from) {
        return switch (from) {
            case OPEN, REOPENED -> EnumSet.of(DEFERRED, ACCEPTED, REJECTED, RESOLVED);
            case DEFERRED -> EnumSet.of(OPEN, ACCEPTED, REJECTED, RESOLVED);
            case ACCEPTED, REJECTED -> EnumSet.of(OPEN);
            case RESOLVED -> EnumSet.noneOf(Status.class);
        };
    }

    public static boolean userAllowed(Status from, Status to) {
        return userTargets(from).contains(to);
    }

    /** Targets of system transitions after a detection run (RESOLVED to RESOLVED = verification confirmed). */
    public static Set<Status> systemTargets(Status from) {
        return switch (from) {
            case OPEN, DEFERRED, REOPENED -> EnumSet.of(RESOLVED);
            case RESOLVED -> EnumSet.of(RESOLVED, REOPENED);
            case ACCEPTED, REJECTED -> EnumSet.of(REOPENED);
        };
    }

    public static boolean systemAllowed(Status from, Status to) {
        return systemTargets(from).contains(to);
    }

    public static boolean reasonRequired(Status to) {
        return to == ACCEPTED || to == REJECTED || to == DEFERRED;
    }

    /** OPEN, REOPENED, DEFERRED and RESOLVED with verification pending may be resolved by non-detection. */
    public static boolean autoResolvable(Status status, String verification) {
        return status == OPEN
                || status == REOPENED
                || status == DEFERRED
                || (status == RESOLVED && "pending".equals(verification));
    }
}
