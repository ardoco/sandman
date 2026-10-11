package edu.kit.kastel.mcse.ardoco.sandman.tlr.ardoco;

import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

import org.junit.jupiter.api.Test;

class VersionTest {

    private static final String HINT =
            " must start with the ardoco.version of the pom. After an ArDoCo update, compare "
                    + "ArdocodeApproach.SadCodeRunner with Ardocode.definePipeline of the new version, rerun the benchmark "
                    + "equivalence tests, then change the version string (and the +aN suffix if the links change).";

    @Test
    void approachVersionsStartWithTheArdocoVersionOfThePom() {
        String ardoco = System.getProperty("ardoco.version");
        assertNotNull(ardoco, "surefire passes ardoco.version");
        assertTrue(ArdocoApproach.VERSION.startsWith(ardoco + "+"), "ardoco " + ArdocoApproach.VERSION + HINT);
        assertTrue(ArdocodeApproach.VERSION.startsWith(ardoco + "+"), "ardocode " + ArdocodeApproach.VERSION + HINT);
    }
}
