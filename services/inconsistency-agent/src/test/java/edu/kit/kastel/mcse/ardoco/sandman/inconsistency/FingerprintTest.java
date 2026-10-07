package edu.kit.kastel.mcse.ardoco.sandman.inconsistency;

import static org.junit.jupiter.api.Assertions.assertEquals;

import edu.kit.kastel.mcse.ardoco.sandman.agent.Ids;
import edu.kit.kastel.mcse.ardoco.sandman.agent.Settings;
import java.util.ArrayList;
import java.util.List;
import org.junit.jupiter.api.Test;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

class FingerprintTest {

    @Test
    void matchesConformanceVectors() {
        JsonNode cases = JsonMapper.shared()
                .readTree(Settings.defaultContractsDir().resolve("conformance/vectors.json"))
                .get("fingerprint")
                .get("cases");
        assertEquals(2, cases.size());
        for (JsonNode c : cases) {
            List<String> keys = new ArrayList<>();
            c.get("input").get("involved").forEach(k -> keys.add(k.asString()));
            String fingerprint = Ids.fingerprint(
                    c.get("input").get("type").asString(),
                    c.get("input").get("detector").asString(),
                    keys);
            assertEquals(c.get("expected").asString(), fingerprint);
            assertEquals(
                    c.get("expectedInconsistencyId").asString(),
                    Ids.stableId("inc", c.get("projectId").asString(), fingerprint));
        }
    }
}
