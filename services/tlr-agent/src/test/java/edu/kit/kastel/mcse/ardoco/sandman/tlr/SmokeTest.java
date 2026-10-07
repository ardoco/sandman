package edu.kit.kastel.mcse.ardoco.sandman.tlr;

import static org.junit.jupiter.api.Assertions.assertTrue;

import org.junit.jupiter.api.Test;

class SmokeTest {

    @Test
    void runsOnJava21OrNewer() {
        assertTrue(Runtime.version().feature() >= 21);
    }
}
