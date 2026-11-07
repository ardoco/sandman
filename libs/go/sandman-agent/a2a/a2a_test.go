package a2a

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"sync"
	"sync/atomic"
	"testing"

	"github.com/ArDoCo/sandman/libs/go/sandman-agent/config"
	"github.com/ArDoCo/sandman/libs/go/sandman-agent/contracts"
	"github.com/ArDoCo/sandman/libs/go/sandman-agent/events"
	"github.com/ArDoCo/sandman/libs/go/sandman-agent/ids"
)

type sink struct {
	mu  sync.Mutex
	got []*events.Event
	c   *contracts.Contracts
}

func (s *sink) Publish(ctx context.Context, typ, subject string, data map[string]any, o events.PublishOptions) (*events.Event, error) {
	e, err := events.BuildEvent(ctx, s.c, "tlr-agent", typ, subject, data, o)
	if err != nil {
		return nil, err
	}
	s.mu.Lock()
	s.got = append(s.got, e)
	s.mu.Unlock()
	return e, nil
}

func (s *sink) reset()     { s.mu.Lock(); s.got = nil; s.mu.Unlock() }
func (s *sink) count() int { s.mu.Lock(); defer s.mu.Unlock(); return len(s.got) }

type vectorCase struct {
	Name              string         `json:"name"`
	Message           map[string]any `json:"message"`
	ExpectedState     string         `json:"expectedState"`
	ExpectedErrorCode string         `json:"expectedErrorCode"`
}

func setup(t *testing.T) (base string, cases []vectorCase, s *sink, runs *atomic.Int32) {
	dir := config.DefaultContractsDir()
	c, err := contracts.New(dir)
	if err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile(filepath.Join(dir, "conformance/vectors.json"))
	if err != nil {
		t.Fatal(err)
	}
	var v struct {
		SkillInvocation struct {
			Cases []vectorCase `json:"cases"`
		} `json:"skillInvocation"`
	}
	if err := json.Unmarshal(raw, &v); err != nil {
		t.Fatal(err)
	}
	s = &sink{c: c}
	runs = &atomic.Int32{}
	skill := Skill{ID: "recover-trace-links", Run: func(_ context.Context, _ *SkillContext, in map[string]any) (*SkillResult, error) {
		runs.Add(1)
		run := map[string]any{"approach": "lissa", "approachVersion": "1.0.0", "runId": "run_0123456789abcdef0123456789abcdef", "status": "completed", "linkCount": float64(0)}
		return &SkillResult{
			Output:    map[string]any{"runs": []any{run}},
			EventType: "sandman.tracelinks.updated",
			EventData: map[string]any{"projectId": in["projectId"], "sourceVersionIds": in["sourceVersionIds"], "targetVersionIds": in["targetVersionIds"], "runs": []any{run}},
		}, nil
	}}
	h, err := Serve(Options{
		AgentCardPath: filepath.Join(dir, "agent-cards/tlr-agent.json"), Skills: []Skill{skill}, Events: s, Contracts: c,
		Settings: &config.Settings{ServiceName: "tlr-agent", ContractsDir: dir, ServiceVerify: map[string]string{"orchestrator": ids.Sha256Hex("secret")}},
	})
	if err != nil {
		t.Fatal(err)
	}
	srv := httptest.NewServer(h)
	t.Cleanup(srv.Close)
	return srv.URL, v.SkillInvocation.Cases, s, runs
}

func send(t *testing.T, base string, msg any, token string) (*http.Response, map[string]any) {
	body, _ := json.Marshal(map[string]any{"jsonrpc": "2.0", "id": 1, "method": "SendMessage", "params": map[string]any{"message": msg}})
	req, _ := http.NewRequest("POST", base+"/a2a", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("A2A-Version", "1.0")
	req.Header.Set("A2A-Extensions", InvocationExtension)
	if token != "" {
		req.Header.Set("Authorization", "Bearer "+token)
	}
	res, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer res.Body.Close()
	var out map[string]any
	_ = json.NewDecoder(res.Body).Decode(&out)
	return res, out
}

func taskOf(t *testing.T, out map[string]any) map[string]any {
	if out["error"] != nil {
		t.Fatalf("rpc error: %v", out["error"])
	}
	r, _ := out["result"].(map[string]any)
	if task, ok := r["task"].(map[string]any); ok {
		return task
	}
	return r
}

func stateOf(task map[string]any) string { return task["status"].(map[string]any)["state"].(string) }

func errorCode(task map[string]any) any {
	m, _ := task["status"].(map[string]any)["message"].(map[string]any)
	md, _ := m["metadata"].(map[string]any)
	e, _ := md[ErrorKey].(map[string]any)
	return e["code"]
}

func find(cases []vectorCase, name string) vectorCase {
	for _, c := range cases {
		if c.Name == name {
			return c
		}
	}
	panic(name)
}

func TestRejectionVectors(t *testing.T) {
	base, cases, _, runs := setup(t)
	n := 0
	for _, c := range cases {
		if c.ExpectedState != "TASK_STATE_REJECTED" {
			continue
		}
		n++
		t.Run(c.Name, func(t *testing.T) {
			res, out := send(t, base, c.Message, "secret")
			task := taskOf(t, out)
			if res.StatusCode != 200 || stateOf(task) != c.ExpectedState || errorCode(task) != c.ExpectedErrorCode {
				t.Fatalf("%d %v", res.StatusCode, out)
			}
		})
	}
	if n == 0 || runs.Load() != 0 {
		t.Fatalf("cases=%d runs=%d", n, runs.Load())
	}
}

func TestValidInvocationCompletesWithOneArtifactAndOneEvent(t *testing.T) {
	base, cases, s, _ := setup(t)
	_, out := send(t, base, find(cases, "valid input is accepted").Message, "secret")
	task := taskOf(t, out)
	arts, _ := task["artifacts"].([]any)
	if stateOf(task) != "TASK_STATE_COMPLETED" || len(arts) != 1 || arts[0].(map[string]any)["name"] != "result" {
		t.Fatalf("%v", out)
	}
	if s.count() != 1 || s.got[0].Type != "sandman.tracelinks.updated" || s.got[0].CorrelationID != "wf_0123456789abcdef0123456789abcdef" || s.got[0].CausationID != "m-1" {
		t.Fatalf("%+v", s.got)
	}
}

func TestRepeatedMessageIDRunsSkillOnce(t *testing.T) {
	base, cases, s, runs := setup(t)
	msg := find(cases, "valid input is accepted").Message
	msg["messageId"] = "m-repeat"
	_, a := send(t, base, msg, "secret")
	_, b := send(t, base, msg, "secret")
	if stateOf(taskOf(t, a)) != "TASK_STATE_COMPLETED" || stateOf(taskOf(t, b)) != "TASK_STATE_COMPLETED" {
		t.Fatalf("%v %v", a, b)
	}
	if runs.Load() != 1 || s.count() != 1 {
		t.Fatalf("runs=%d events=%d", runs.Load(), s.count())
	}
	msg2 := map[string]any{}
	for k, v := range msg {
		msg2[k] = v
	}
	msg2["parts"] = []any{map[string]any{"data": map[string]any{"projectId": "other", "sourceVersionIds": []any{"ver_0123456789abcdef0123456789abcdef"}, "targetVersionIds": []any{"ver_fedcba9876543210fedcba9876543210"}}}}
	_, c := send(t, base, msg2, "secret")
	if task := taskOf(t, c); stateOf(task) != "TASK_STATE_REJECTED" || errorCode(task) != "INVALID_INPUT" {
		t.Fatalf("%v", c)
	}
}

func TestUnauthorizedRequests(t *testing.T) {
	base, cases, _, _ := setup(t)
	for _, tok := range []string{"", "wrong"} {
		if res, _ := send(t, base, cases[0].Message, tok); res.StatusCode != 401 {
			t.Fatalf("token %q: %d", tok, res.StatusCode)
		}
	}
}

func TestAgentCardAndStatusRoute(t *testing.T) {
	base, _, _, _ := setup(t)
	res, err := http.Get(base + "/.well-known/agent-card.json")
	if err != nil {
		t.Fatal(err)
	}
	defer res.Body.Close()
	var card map[string]any
	_ = json.NewDecoder(res.Body).Decode(&card)
	if card["name"] != "sandman-tlr-agent" {
		t.Fatalf("%v", card)
	}
	req, _ := http.NewRequest("GET", base+"/v1/status", nil)
	req.Header.Set("Authorization", "Bearer secret")
	if r, _ := http.DefaultClient.Do(req); r.StatusCode != 200 {
		t.Fatalf("status %d", r.StatusCode)
	}
	if r, _ := http.Get(base + "/v1/status"); r.StatusCode != 401 {
		t.Fatalf("status %d", r.StatusCode)
	}
}
