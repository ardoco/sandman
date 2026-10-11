package edu.kit.kastel.mcse.ardoco.sandman.tlr;

import static org.junit.jupiter.api.Assertions.assertEquals;

import java.nio.file.Files;
import java.nio.file.Path;
import org.junit.jupiter.api.Test;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

/** The card of the tests is the tlr-agent card plus the stand-in for the plugin skill, nothing else. */
class TestCardTest {

    @Test
    void testCardIsTheContractCardPlusTheCodeModelSkill() throws Exception {
        var mapper = JsonMapper.shared();
        ObjectNode contract =
                (ObjectNode) mapper.readTree(Files.readString(Path.of("../../contracts/agent-cards/tlr-agent.json")));
        ObjectNode test = (ObjectNode)
                mapper.readTree(Files.readString(Path.of("src/test/resources/tlr-agent-with-code-model.json")));
        ((ObjectNode) test.at("/capabilities/extensions/0/params/skills")).remove("sandman.plugin.code-model");
        ((tools.jackson.databind.node.ArrayNode) test.get("skills"))
                .remove(test.get("skills").size() - 1);
        assertEquals(contract, test);
    }
}
