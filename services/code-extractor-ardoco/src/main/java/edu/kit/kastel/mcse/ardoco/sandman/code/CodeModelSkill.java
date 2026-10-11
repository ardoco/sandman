package edu.kit.kastel.mcse.ardoco.sandman.code;

import edu.kit.kastel.mcse.ardoco.sandman.agent.a2a.Skill;
import edu.kit.kastel.mcse.ardoco.sandman.agent.a2a.SkillContext;
import edu.kit.kastel.mcse.ardoco.sandman.agent.a2a.SkillException;
import edu.kit.kastel.mcse.ardoco.sandman.agent.a2a.SkillResult;
import jakarta.inject.Inject;
import jakarta.inject.Singleton;
import tools.jackson.databind.JsonNode;

/** Plugin skill {@code sandman.plugin.code-model}: the ACM of the project's current code, as ArDoCo writes it. */
@Singleton
public class CodeModelSkill implements Skill {

    @Inject
    CodeService service;

    @Override
    public String id() {
        return "sandman.plugin.code-model";
    }

    @Override
    public SkillResult run(SkillContext ctx, JsonNode input) throws SkillException {
        return SkillResult.of(service.codeModel(input));
    }
}
