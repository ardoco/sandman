package edu.kit.kastel.mcse.ardoco.sandman.agent.a2a;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;

import edu.kit.kastel.mcse.ardoco.sandman.agent.Ids;
import edu.kit.kastel.mcse.ardoco.sandman.agent.ServiceAuth;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.Test;
import tools.jackson.databind.json.JsonMapper;

class UnitTest {

    @Test
    void pluginModeChecksTheInboundToken() {
        ServiceAuth none = new ServiceAuth(Map.of(), null);
        assertEquals("owner", A2aServer.caller(none, "in-token", "Bearer in-token"));
        assertNull(A2aServer.caller(none, "in-token", "Bearer other"));
        assertNull(A2aServer.caller(none, "in-token", null));
    }

    @Test
    void pluginModeWithOneTokenPerCallerNamesTheCaller() {
        ServiceAuth none = new ServiceAuth(Map.of(), null);
        Map<String, String> tokens = Map.of("extraction-agent", "tok-a", "tlr-agent", "tok-b");
        assertEquals("extraction-agent", A2aServer.caller(none, null, tokens, "Bearer tok-a"));
        assertEquals("tlr-agent", A2aServer.caller(none, null, tokens, "bearer tok-b"));
        assertNull(A2aServer.caller(none, null, tokens, "Bearer tok-c"));
        assertNull(A2aServer.caller(none, null, tokens, "Bearer tok-"));
        assertNull(A2aServer.caller(none, null, tokens, "Bearer "));
        assertNull(A2aServer.caller(none, null, tokens, null));
    }

    @Test
    void agentModeRejectsPluginCallers() {
        ServiceAuth auth =
                new ServiceAuth(Map.of("orchestrator", Ids.sha256Hex("o"), "plugin:x", Ids.sha256Hex("p")), null);
        assertEquals("orchestrator", A2aServer.caller(auth, null, "Bearer o"));
        assertNull(A2aServer.caller(auth, null, "Bearer p"));
        assertNull(A2aServer.caller(auth, null, "Bearer wrong"));
    }

    @Test
    void integralDoublesBecomeIntegers() {
        var json = SkillExecutor.toJson(Map.of("a", 1.0, "b", List.of(2.5, 3.0)));
        assertEquals(JsonMapper.shared().readTree("{\"a\":1,\"b\":[2.5,3]}"), json);
    }
}
