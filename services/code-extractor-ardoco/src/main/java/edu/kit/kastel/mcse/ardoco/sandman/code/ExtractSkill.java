package edu.kit.kastel.mcse.ardoco.sandman.code;

import edu.kit.kastel.mcse.ardoco.sandman.agent.a2a.Skill;
import edu.kit.kastel.mcse.ardoco.sandman.agent.a2a.SkillContext;
import edu.kit.kastel.mcse.ardoco.sandman.agent.a2a.SkillException;
import edu.kit.kastel.mcse.ardoco.sandman.agent.a2a.SkillResult;
import jakarta.inject.Inject;
import jakarta.inject.Singleton;
import tools.jackson.databind.JsonNode;

/** Plugin skill {@code sandman.plugin.extract}: CodeUnit and TestCase elements from ArDoCo's code model. */
@Singleton
public class ExtractSkill implements Skill {

    @Inject
    CodeService service;

    @Override
    public String id() {
        return "sandman.plugin.extract";
    }

    @Override
    public SkillResult run(SkillContext ctx, JsonNode input) throws SkillException {
        return SkillResult.of(service.extract(input));
    }
}
