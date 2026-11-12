package edu.kit.kastel.mcse.ardoco.sandman.tlr;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import edu.kit.kastel.mcse.ardoco.sandman.agent.Contracts;
import edu.kit.kastel.mcse.ardoco.sandman.agent.Settings;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Set;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

class ApproachesFallbackTest {

    private record Fake(String id) implements TraceLinkApproach {
        public String version() {
            return "1";
        }

        public Set<KindPair> supports() {
            return Set.of();
        }

        public List<CandidateLink> recover(TlrInput in, TlrContext ctx) {
            return List.of();
        }
    }

    private static Approaches load(Path dir) throws Exception {
        Path file = dir.resolve("approaches.yaml");
        Files.writeString(
                file,
                "approaches:\n  artemis: { enabled: true }\n  ardoco: { enabled: true, fallbackFor: artemis }\n"
                        + "  lissa: { enabled: true }\n");
        return new Approaches(
                file,
                List.of(new Fake("artemis"), new Fake("ardoco"), new Fake("lissa")),
                new Contracts(Settings.defaultContractsDir()));
    }

    private static List<String> ids(List<Approaches.Entry> entries) {
        return entries.stream().map(e -> e.approach().id()).toList();
    }

    @Test
    void llmRouteRunsPrimaryAndSkipsFallback(@TempDir Path dir) throws Exception {
        Approaches a = load(dir);
        assertEquals(List.of("artemis", "lissa"), ids(a.defaults(true)));
        assertTrue(a.skipped(true).get("ardoco").contains("an LLM route exists"));
    }

    @Test
    void noLlmRouteRunsFallbackInstead(@TempDir Path dir) throws Exception {
        Approaches a = load(dir);
        assertEquals(List.of("ardoco", "lissa"), ids(a.defaults(false)));
        assertTrue(a.skipped(false).get("artemis").contains("fallback approach runs"));
    }
}
