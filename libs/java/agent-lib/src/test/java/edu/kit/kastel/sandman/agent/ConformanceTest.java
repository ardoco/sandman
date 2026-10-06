package edu.kit.kastel.sandman.agent;

import static org.junit.jupiter.api.Assertions.assertEquals;

import edu.kit.kastel.sandman.agent.testing.ConformanceRunner;
import java.nio.file.Path;
import java.util.List;
import org.junit.jupiter.api.Test;

class ConformanceTest {

    @Test
    void passesSharedVectors() {
        Path dir = Settings.defaultContractsDir();
        assertEquals(List.of(), ConformanceRunner.run(dir.resolve("conformance/vectors.json"), new Contracts(dir)));
    }
}
