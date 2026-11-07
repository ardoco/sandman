package edu.kit.kastel.mcse.ardoco.sandman.inconsistency;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertSame;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import edu.kit.kastel.mcse.ardoco.sandman.agent.Contracts;
import edu.kit.kastel.mcse.ardoco.sandman.agent.Settings;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.util.List;
import java.util.Map;
import java.util.Set;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

class DetectorRegistryTest {

    static Contracts contracts;

    @BeforeAll
    static void contracts() {
        contracts = new Contracts(Settings.defaultContractsDir());
    }

    private static Path yaml(Path dir, String content) throws Exception {
        return Files.writeString(dir.resolve("plugins.yaml"), content);
    }

    @Test
    void durationsUseEachUnit() {
        assertEquals(Duration.ofMillis(250), DetectorRegistry.duration("250ms"));
        assertEquals(Duration.ofSeconds(30), DetectorRegistry.duration("30s"));
        assertEquals(Duration.ofMinutes(10), DetectorRegistry.duration("10m"));
        assertEquals(Duration.ofHours(2), DetectorRegistry.duration("2h"));
    }

    @Test
    void invalidDurationsAreRejected() {
        for (String text : List.of("", "10", "m", "1d", "x10s", "10s ")) {
            var e = assertThrows(Settings.ConfigException.class, () -> DetectorRegistry.duration(text));
            assertEquals("invalid duration '" + text + "'", e.getMessage());
        }
    }

    @Test
    void lookupReturnsTheDetectorOrNullAndParamsDefaultToEmpty() {
        var stub = new StubUntracedDetector();
        var registry = new DetectorRegistry(List.of(stub));
        assertSame(stub, registry.get("stub-untraced"));
        assertNull(registry.get("other"));
        assertEquals(List.of(stub), registry.all());
        assertEquals(Map.of(), registry.params("stub-untraced"));
    }

    @Test
    void enabledDetectorsKeepTheirParamsAndDisabledOnesAreSkipped(@TempDir Path dir) throws Exception {
        Path file = yaml(
                dir,
                "detectors:\n"
                        + "  stub-untraced: { enabled: false }\n"
                        + "  ardoco-id: { enabled: true, params: { linkApproaches: all } }\n"
                        + "  model-code: { enabled: true }\n");
        var registry = DetectorRegistry.load(file, contracts);
        assertEquals(
                List.of("ardoco-id", "model-code"),
                registry.all().stream().map(InconsistencyDetector::id).toList());
        assertEquals(Map.of("linkApproaches", "all"), registry.params("ardoco-id"));
        assertEquals(Map.of(), registry.params("model-code"));
        assertEquals(Map.of(), registry.params("stub-untraced"));
    }

    @Test
    void unknownDetectorsAndParamsFailAtStartup(@TempDir Path dir) throws Exception {
        Path unknown = yaml(dir, "detectors:\n  nope: { enabled: true }\n");
        var e = assertThrows(Settings.ConfigException.class, () -> DetectorRegistry.load(unknown, contracts));
        assertEquals("unknown detector 'nope' in " + unknown, e.getMessage());

        Path badParam = yaml(dir, "detectors:\n  stub-untraced: { enabled: true, params: { x: 1 } }\n");
        e = assertThrows(Settings.ConfigException.class, () -> DetectorRegistry.load(badParam, contracts));
        assertEquals("detector 'stub-untraced': unknown param 'x' in " + badParam + " (known: [])", e.getMessage());
    }

    @Test
    void unreadableTokenFileFailsAtStartup(@TempDir Path dir) throws Exception {
        Path file = yaml(
                dir,
                "plugins:\n  - { id: p, transport: http, url: 'http://x', tokenFile: '" + dir.resolve("missing")
                        + "' }\n");
        var e = assertThrows(Settings.ConfigException.class, () -> DetectorRegistry.load(file, contracts));
        assertTrue(e.getMessage().startsWith("plugin 'p': cannot read tokenFile: "), e.getMessage());
    }

    @Test
    void stubDetectorDescribesItself() {
        var stub = new StubUntracedDetector();
        assertEquals("stub-untraced", stub.id());
        assertEquals("0.1.0", stub.version());
        assertEquals(Set.of("untraced-requirement"), stub.types());
        assertEquals("Requirements without an active, non-rejected trace link", stub.description());
    }
}
