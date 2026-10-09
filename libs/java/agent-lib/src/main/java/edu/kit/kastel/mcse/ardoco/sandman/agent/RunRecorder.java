package edu.kit.kastel.mcse.ardoco.sandman.agent;

import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** Creates and finishes Run nodes (contracts/kg/schema.yaml). */
public final class RunRecorder {

    /** Properties of a new run; nullable fields are omitted. */
    public record RunInfo(
            String projectId,
            String skill,
            String tool,
            String toolVersion,
            String inputKey,
            String model,
            String params,
            String configHash,
            List<String> usedVersionIds,
            String workflowId,
            String taskId,
            String triggeredBy,
            String replayMode) {

        public static RunInfo of(String projectId, String skill, String tool, String toolVersion, String inputKey) {
            return new RunInfo(
                    projectId, skill, tool, toolVersion, inputKey, null, null, null, List.of(), null, null, null, null);
        }
    }

    private final KgClient kg;
    private final String agent;
    private final String agentVersion;

    public RunRecorder(KgClient kg, String agent, String agentVersion) {
        this.kg = kg;
        this.agent = agent;
        this.agentVersion = agentVersion;
    }

    /** Creates a running Run with USED edges; returns its id. A null replayMode defaults to SANDMAN_LLM_MODE. */
    public String start(RunInfo run) {
        String runId = Ids.newRunId();
        Map<String, Object> props = new LinkedHashMap<>();
        props.put("id", runId);
        props.put("projectId", run.projectId());
        props.put("agent", agent);
        props.put("agentVersion", agentVersion);
        props.put("skill", run.skill());
        props.put("tool", run.tool());
        props.put("toolVersion", run.toolVersion());
        props.put("inputKey", run.inputKey());
        props.put("model", run.model());
        props.put("params", run.params());
        props.put("configHash", run.configHash());
        props.put("workflowId", run.workflowId());
        props.put("a2aTaskId", run.taskId());
        props.put("triggeredBy", run.triggeredBy());
        props.put("replayMode", run.replayMode() != null ? run.replayMode() : Settings.llmMode(System.getenv()));
        props.put("status", "running");
        props.put("startedAt", Event.now());
        props.values().removeIf(v -> v == null);
        kg.write(
                "CREATE (r:Run) SET r = $props "
                        + "WITH r UNWIND $versions AS vid MATCH (v:ArtifactVersion {id: vid}) CREATE (r)-[:USED]->(v)",
                Map.of("props", props, "versions", run.usedVersionIds() != null ? run.usedVersionIds() : List.of()));
        return runId;
    }

    public void complete(String runId) {
        complete(runId, "completed", null, null);
    }

    /** Marks a run completed (or partial) with token usage. */
    public void complete(String runId, String status, Long inputTokens, Long outputTokens) {
        Map<String, Object> params = new HashMap<>();
        params.put("id", runId);
        params.put("status", status);
        params.put("now", Event.now());
        params.put("inp", inputTokens);
        params.put("out", outputTokens);
        kg.write(
                "MATCH (r:Run {id: $id}) SET r.status = $status, r.endedAt = $now, "
                        + "r.inputTokens = $inp, r.outputTokens = $out",
                params);
    }

    public void fail(String runId, String error) {
        kg.write(
                "MATCH (r:Run {id: $id}) SET r.status = 'failed', r.error = $error, r.endedAt = $now",
                Map.of("id", runId, "error", error, "now", Event.now()));
    }

    /** Deletes facts created by non-completed runs with this input key; marks those runs failed. */
    public long cleanupStale(String inputKey) {
        List<Map<String, Object>> rows = kg.write(
                "MATCH (r:Run {inputKey: $key}) WHERE r.status IN ['running', 'failed'] "
                        + "OPTIONAL MATCH (r)-[:PRODUCED|DETECTED]->(f) WHERE f.runId = r.id "
                        + "WITH r, collect(f) AS facts "
                        + "SET r.status = 'failed', r.endedAt = coalesce(r.endedAt, $now) "
                        + "WITH facts UNWIND facts AS f DETACH DELETE f RETURN count(f) AS deleted",
                Map.of("key", inputKey, "now", Event.now()));
        return rows.isEmpty() ? 0 : ((Number) rows.get(0).get("deleted")).longValue();
    }
}
