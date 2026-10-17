package edu.kit.kastel.mcse.ardoco.sandman.inconsistency;

import edu.kit.kastel.mcse.ardoco.sandman.agent.a2a.Skill;
import edu.kit.kastel.mcse.ardoco.sandman.agent.a2a.SkillContext;
import edu.kit.kastel.mcse.ardoco.sandman.agent.a2a.SkillException;
import edu.kit.kastel.mcse.ardoco.sandman.agent.a2a.SkillResult;
import jakarta.enterprise.context.ApplicationScoped;
import jakarta.inject.Inject;
import java.util.ArrayList;
import java.util.List;
import tools.jackson.databind.JsonNode;

/** A2A skill detect-inconsistencies. */
@ApplicationScoped
public class DetectSkill implements Skill {

    @Inject
    DetectionService detection;

    @Override
    public String id() {
        return "detect-inconsistencies";
    }

    @Override
    public SkillResult run(SkillContext ctx, JsonNode input) throws SkillException {
        var result = detection.detect(new DetectionService.Request(
                input.get("projectId").asString(),
                strings(input.get("scopeArtifactIds")),
                strings(input.get("detectors")),
                ctx.workflowId(),
                ctx.taskId(),
                ctx.actor(),
                ctx::cancelled));
        return new SkillResult(result.output(), "sandman.inconsistencies.updated", result.eventData());
    }

    private static List<String> strings(JsonNode array) {
        if (array == null) {
            return null;
        }
        List<String> out = new ArrayList<>();
        array.forEach(n -> out.add(n.asString()));
        return out;
    }
}
