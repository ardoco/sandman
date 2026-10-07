package edu.kit.kastel.mcse.ardoco.sandman.agent.a2a;

import tools.jackson.databind.JsonNode;

/** A skill as CDI bean; input/output schemas come from the Agent Card's skill-invocation extension. */
public interface Skill {

    String id();

    /** Does the work; throw SkillException for expected failures. */
    SkillResult run(SkillContext ctx, JsonNode input) throws SkillException;
}
