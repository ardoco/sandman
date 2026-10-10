package edu.kit.kastel.mcse.ardoco.sandman.inconsistency;

import static edu.kit.kastel.mcse.ardoco.sandman.inconsistency.Lifecycle.Status.*;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import edu.kit.kastel.mcse.ardoco.sandman.inconsistency.Lifecycle.Status;
import java.util.List;
import java.util.Map;
import java.util.Set;
import org.junit.jupiter.api.Test;

class LifecycleTest {

    static final Map<Status, Set<Status>> USER = Map.of(
            OPEN, Set.of(DEFERRED, ACCEPTED, REJECTED, RESOLVED),
            REOPENED, Set.of(DEFERRED, ACCEPTED, REJECTED, RESOLVED),
            DEFERRED, Set.of(OPEN, ACCEPTED, REJECTED, RESOLVED),
            ACCEPTED, Set.of(OPEN),
            REJECTED, Set.of(OPEN),
            RESOLVED, Set.of(OPEN));
    static final Map<Status, Set<Status>> SYSTEM = Map.of(
            OPEN, Set.of(RESOLVED),
            DEFERRED, Set.of(RESOLVED),
            REOPENED, Set.of(RESOLVED),
            RESOLVED, Set.of(RESOLVED, REOPENED),
            ACCEPTED, Set.of(REOPENED),
            REJECTED, Set.of(REOPENED));

    @Test
    void everyPairIsAllowedOrForbiddenForUserAndSystem() {
        for (Status from : Status.values()) {
            for (Status to : Status.values()) {
                assertEquals(USER.get(from).contains(to), Lifecycle.userAllowed(from, to), "user " + from + "->" + to);
                assertEquals(
                        SYSTEM.get(from).contains(to), Lifecycle.systemAllowed(from, to), "system " + from + "->" + to);
            }
        }
    }

    @Test
    void reasonIsRequiredForAcceptRejectDefer() {
        for (Status to : Status.values()) {
            assertEquals(List.of(ACCEPTED, REJECTED, DEFERRED).contains(to), Lifecycle.reasonRequired(to));
        }
    }

    @Test
    void autoResolvableStates() {
        assertTrue(Lifecycle.autoResolvable(OPEN, "none"));
        assertTrue(Lifecycle.autoResolvable(REOPENED, "none"));
        assertTrue(Lifecycle.autoResolvable(DEFERRED, "none"));
        assertTrue(Lifecycle.autoResolvable(RESOLVED, "pending"));
        assertFalse(Lifecycle.autoResolvable(RESOLVED, "confirmed"));
        assertFalse(Lifecycle.autoResolvable(ACCEPTED, "none"));
        assertFalse(Lifecycle.autoResolvable(REJECTED, "none"));
    }
}
