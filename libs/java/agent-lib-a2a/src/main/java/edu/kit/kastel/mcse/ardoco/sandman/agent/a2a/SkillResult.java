package edu.kit.kastel.mcse.ardoco.sandman.agent.a2a;

import tools.jackson.databind.JsonNode;

/** Skill output plus the task's single completion event (type and data null for plugin skills). */
public record SkillResult(JsonNode output, String eventType, JsonNode eventData) {

    /** Result without completion event (plugin skills). */
    public static SkillResult of(JsonNode output) {
        return new SkillResult(output, null, null);
    }
}
