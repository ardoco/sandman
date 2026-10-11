package edu.kit.kastel.mcse.ardoco.sandman.tlr;

import edu.kit.kastel.mcse.ardoco.sandman.agent.a2a.Skill;
import edu.kit.kastel.mcse.ardoco.sandman.agent.a2a.SkillContext;
import edu.kit.kastel.mcse.ardoco.sandman.agent.a2a.SkillException;
import edu.kit.kastel.mcse.ardoco.sandman.agent.a2a.SkillResult;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.ardoco.CodeModels;
import jakarta.inject.Singleton;
import java.nio.file.Path;
import java.util.Set;
import java.util.TreeSet;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

/**
 * Stand-in for the plugin skill {@code sandman.plugin.code-model}, served by the tlr-agent under test (the test card
 * lists it), so that approach ardocode goes through the real A2A client. It answers with a benchmark ACM and checks
 * that the request lists exactly the files of that ACM.
 */
@Singleton
public class CodeModelStubSkill implements Skill {

    /** The ACM to return, or null; set by the test. */
    static volatile Path acm;

    static volatile int calls;

    @Override
    public String id() {
        return "sandman.plugin.code-model";
    }

    @Override
    public SkillResult run(SkillContext ctx, JsonNode input) throws SkillException {
        calls++;
        Set<String> requested = new TreeSet<>();
        input.get("currentArtifacts").forEach(a -> requested.add(a.get("path").asString()));
        Set<String> expected = new TreeSet<>(CodeModels.compilationUnits(acm).values());
        if (!requested.equals(expected)) {
            throw new SkillException(
                    "INVALID_INPUT",
                    "requested " + requested.size() + " files, the code model has " + expected.size(),
                    false);
        }
        return SkillResult.of(JsonMapper.shared().createObjectNode().set("acm", CodeModels.read(acm)));
    }
}
