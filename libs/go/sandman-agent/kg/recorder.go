package kg

import (
	"context"

	"github.com/ArDoCo/sandman/libs/go/sandman-agent/ids"
)

type Usage struct{ InputTokens, OutputTokens int64 }

type RunStart struct {
	ProjectID, Skill, Tool, ToolVersion, InputKey string
	Model, Params, ConfigHash                     string
	UsedVersionIDs                                []string
	WorkflowID, TaskID, TriggeredBy, ReplayMode   string
	PromptIDs                                     []string
}

type Writer interface {
	Write(ctx context.Context, cypher string, params map[string]any) ([]Row, error)
}

type RunRecorder struct {
	KG                  Writer
	Agent, AgentVersion string
	// DefaultReplayMode is used when RunStart.ReplayMode is empty.
	DefaultReplayMode string
}

func (r *RunRecorder) Start(ctx context.Context, s RunStart) (string, error) {
	id := ids.NewRunID()
	mode := s.ReplayMode
	if mode == "" {
		mode = r.DefaultReplayMode
	}
	props := map[string]any{
		"id": id, "projectId": s.ProjectID, "agent": r.Agent, "agentVersion": r.AgentVersion, "skill": s.Skill, "tool": s.Tool,
		"toolVersion": s.ToolVersion, "inputKey": s.InputKey, "status": "running", "startedAt": Now(),
	}
	for k, v := range map[string]string{"model": s.Model, "params": s.Params, "configHash": s.ConfigHash, "workflowId": s.WorkflowID,
		"a2aTaskId": s.TaskID, "triggeredBy": s.TriggeredBy, "replayMode": mode} {
		if v != "" {
			props[k] = v
		}
	}
	if len(s.PromptIDs) > 0 {
		props["promptIds"] = s.PromptIDs
	}
	versions := s.UsedVersionIDs
	if versions == nil {
		versions = []string{}
	}
	_, err := r.KG.Write(ctx, "CREATE (r:Run) SET r = $props "+
		"WITH r UNWIND $versions AS vid MATCH (v:ArtifactVersion {id: vid}) CREATE (r)-[:USED]->(v)",
		map[string]any{"props": props, "versions": versions})
	return id, err
}

func (r *RunRecorder) Complete(ctx context.Context, runID string, usage *Usage, status, resolvedModel string, mixedModels bool) error {
	if status == "" {
		status = "completed"
	}
	params := map[string]any{"id": runID, "status": status, "now": Now(), "inp": nil, "out": nil, "resolved": nil, "mixed": nil}
	if resolvedModel != "" {
		params["resolved"] = resolvedModel
	}
	if mixedModels {
		params["mixed"] = true
	}
	if usage != nil {
		params["inp"], params["out"] = usage.InputTokens, usage.OutputTokens
	}
	_, err := r.KG.Write(ctx, "MATCH (r:Run {id: $id}) SET r.status = $status, r.endedAt = $now, "+
		"r.inputTokens = $inp, r.outputTokens = $out, r.resolvedModel = $resolved, r.mixedModels = $mixed", params)
	return err
}

func (r *RunRecorder) Fail(ctx context.Context, runID, message string) error {
	_, err := r.KG.Write(ctx, "MATCH (r:Run {id: $id}) SET r.status = 'failed', r.error = $error, r.endedAt = $now",
		map[string]any{"id": runID, "error": message, "now": Now()})
	return err
}

// CleanupStale fails stale runs of inputKey and deletes their facts. It returns the number of deleted facts.
func (r *RunRecorder) CleanupStale(ctx context.Context, inputKey string) (int64, error) {
	rows, err := r.KG.Write(ctx, "MATCH (r:Run {inputKey: $key}) WHERE r.status IN ['running', 'failed'] "+
		"OPTIONAL MATCH (r)-[:PRODUCED|DETECTED]->(f) WHERE f.runId = r.id "+
		"WITH r, collect(f) AS facts "+
		"SET r.status = 'failed', r.endedAt = coalesce(r.endedAt, $now) "+
		"WITH facts UNWIND facts AS f DETACH DELETE f RETURN count(f) AS deleted",
		map[string]any{"key": inputKey, "now": Now()})
	if err != nil || len(rows) == 0 {
		return 0, err
	}
	n, _ := rows[0]["deleted"].(int64)
	return n, nil
}
