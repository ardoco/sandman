// Package a2a serves SANDMAN skills over A2A (a2a-go v2): skill id in message metadata, one data part in, one "result" artifact out.
package a2a

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"iter"
	"net/http"
	"net/url"
	"os"
	"sync"
	"time"

	"github.com/ArDoCo/sandman/libs/go/sandman-agent/auth"
	"github.com/ArDoCo/sandman/libs/go/sandman-agent/config"
	"github.com/ArDoCo/sandman/libs/go/sandman-agent/contracts"
	"github.com/ArDoCo/sandman/libs/go/sandman-agent/events"
	"github.com/ArDoCo/sandman/libs/go/sandman-agent/ids"
	"github.com/ArDoCo/sandman/libs/go/sandman-agent/llm"
	"github.com/ArDoCo/sandman/libs/go/sandman-agent/status"
	"github.com/ArDoCo/sandman/libs/go/sandman-agent/telemetry"
	a2a "github.com/a2aproject/a2a-go/v2/a2a"
	"github.com/a2aproject/a2a-go/v2/a2asrv"
)

const (
	SkillKey            = "sandman.skill"
	WorkflowKey         = "sandman.workflowId"
	ActorKey            = "sandman.actor"
	ErrorKey            = "sandman.error"
	InvocationExtension = "https://sandman.invalid/a2a/extensions/skill-invocation/v1"
	maxSeen             = 1024
	activityInterval    = 2 * time.Second
)

type SkillError struct {
	Code      string
	Msg       string
	Retryable bool
}

func (e *SkillError) Error() string { return e.Msg }

func (e *SkillError) json() map[string]any {
	return map[string]any{"code": e.Code, "message": e.Msg, "retryable": e.Retryable}
}

type SkillResult struct {
	Output    map[string]any
	EventType string
	EventData map[string]any
}

type SkillContext struct {
	TaskID, MessageID, WorkflowID, Caller, Actor string
	Settings                                     *config.Settings
	Progress                                     func(text string)
}

type Skill struct {
	ID  string
	Run func(ctx context.Context, sc *SkillContext, input map[string]any) (*SkillResult, error)
}

type Options struct {
	AgentCardPath string
	Skills        []Skill
	Settings      *config.Settings
	Events        events.Sink
	Contracts     *contracts.Contracts
	Status        *status.Tracker
}

type schemaRefs struct{ Input, Output string }

type outcome struct {
	output map[string]any
	err    *SkillError
	reject bool
}

type seenEntry struct {
	fingerprint string
	done        chan struct{}
	out         outcome
	settled     bool
}

type executor struct {
	skills    map[string]Skill
	schemas   map[string]schemaRefs
	planned   map[string]bool
	settings  *config.Settings
	contracts *contracts.Contracts
	events    events.Sink
	status    *status.Tracker

	mu      sync.Mutex
	seen    map[string]*seenEntry
	order   []string
	running map[a2a.TaskID]context.CancelFunc
}

var _ a2asrv.AgentExecutor = (*executor)(nil)

func textPart(s string) *a2a.Part { p := a2a.NewTextPart(s); return p }

func dataPart(v any) *a2a.Part { return a2a.NewDataPart(v) }

func (x *executor) agentMessage(ec *a2asrv.ExecutorContext, meta map[string]any, parts ...*a2a.Part) *a2a.Message {
	m := a2a.NewMessageForTask(a2a.MessageRoleAgent, ec, parts...)
	m.Metadata = meta
	return m
}

func (x *executor) end(ec *a2asrv.ExecutorContext, o outcome, yield func(a2a.Event, error) bool) {
	if o.err != nil {
		state := a2a.TaskStateFailed
		if o.reject {
			state = a2a.TaskStateRejected
		}
		msg := x.agentMessage(ec, map[string]any{ErrorKey: o.err.json()}, textPart(o.err.Msg))
		yield(a2a.NewStatusUpdateEvent(ec, state, msg), nil)
		return
	}
	out := o.output
	if out == nil {
		out = map[string]any{}
	}
	art := a2a.NewArtifactEvent(ec, dataPart(out))
	art.Artifact.Name = "result"
	art.LastChunk = true
	if !yield(art, nil) {
		return
	}
	yield(a2a.NewStatusUpdateEvent(ec, a2a.TaskStateCompleted, nil), nil)
}

func (x *executor) Execute(ctx context.Context, ec *a2asrv.ExecutorContext) iter.Seq2[a2a.Event, error] {
	return func(yield func(a2a.Event, error) bool) {
		if ec.StoredTask == nil {
			if !yield(a2a.NewSubmittedTask(ec, ec.Message), nil) {
				return
			}
		}
		reject := func(code, text string) {
			x.end(ec, outcome{err: &SkillError{code, text, false}, reject: true}, yield)
		}
		msg := ec.Message
		skillID, _ := msg.Metadata[SkillKey].(string)
		if skillID == "" {
			reject("INVALID_INPUT", "metadata "+SkillKey+" missing")
			return
		}
		skill, have := x.skills[skillID]
		if !have && x.planned[skillID] {
			reject("NOT_IMPLEMENTED", fmt.Sprintf("skill %s is planned, not implemented", skillID))
			return
		}
		schema, hasSchema := x.schemas[skillID]
		if !have || !hasSchema {
			reject("UNKNOWN_SKILL", "unknown skill "+skillID)
			return
		}
		var dataParts []*a2a.Part
		for _, p := range msg.Parts {
			if _, ok := p.Content.(a2a.Data); ok {
				dataParts = append(dataParts, p)
			}
		}
		if len(dataParts) != 1 {
			reject("INVALID_INPUT", "expected exactly one data part")
			return
		}
		input, _ := dataParts[0].Data().(map[string]any)
		if input == nil {
			reject("INVALID_INPUT", "data part must be an object")
			return
		}
		if err := x.contracts.Validate(schema.Input, input); err != nil {
			var ce *contracts.Error
			if errors.As(err, &ce) {
				reject("INVALID_INPUT", ce.Error())
				return
			}
			yield(nil, err)
			return
		}
		caller := ""
		if ec.User != nil {
			caller = ec.User.Name
		}
		actor, err := auth.ActorFromMetadata(caller, msg.Metadata)
		if err != nil {
			reject("FORBIDDEN", err.Error())
			return
		}

		key := caller + "\x1f" + msg.ID
		fp, err := ids.CanonicalJSON([]any{skillID, input})
		if err != nil {
			yield(nil, err)
			return
		}
		x.mu.Lock()
		if prev, ok := x.seen[key]; ok {
			x.mu.Unlock()
			if prev.fingerprint != fp {
				reject("INVALID_INPUT", "messageId reused with a different request")
				return
			}
			if !yield(a2a.NewStatusUpdateEvent(ec, a2a.TaskStateWorking, nil), nil) {
				return
			}
			select {
			case <-prev.done:
				x.end(ec, prev.out, yield)
			case <-ctx.Done():
			}
			return
		}
		entry := &seenEntry{fingerprint: fp, done: make(chan struct{})}
		x.seen[key] = entry
		x.order = append(x.order, key)
		for len(x.seen) > maxSeen && len(x.order) > 0 {
			k := x.order[0]
			x.order = x.order[1:]
			if e, ok := x.seen[k]; ok && e.settled {
				delete(x.seen, k)
			}
		}
		runCtx, cancel := context.WithCancel(ctx)
		x.running[ec.TaskID] = cancel
		x.mu.Unlock()
		defer func() {
			cancel()
			x.mu.Lock()
			entry.settled = true
			delete(x.running, ec.TaskID)
			x.mu.Unlock()
		}()

		workflowID, _ := msg.Metadata[WorkflowKey].(string)
		var lastProgress time.Time
		sc := &SkillContext{
			TaskID: string(ec.TaskID), MessageID: msg.ID, WorkflowID: workflowID, Caller: caller, Actor: actor,
			Settings: x.settings,
			Progress: func(text string) {
				if time.Since(lastProgress) < activityInterval {
					return
				}
				lastProgress = time.Now()
				yield(a2a.NewStatusUpdateEvent(ec, a2a.TaskStateWorking, x.agentMessage(ec, nil, textPart(text))), nil)
			},
		}
		var out outcome
		_ = x.status.Track(func() error {
			if !yield(a2a.NewStatusUpdateEvent(ec, a2a.TaskStateWorking, nil), nil) {
				return nil
			}
			_ = telemetry.WithSpan(runCtx, "sandman.a2a.task",
				map[string]string{"sandman.skill": skillID, "sandman.task_id": string(ec.TaskID)}, "",
				func(c context.Context) error { out = x.run(c, skill, schema.Output, sc, input); return nil })
			return nil
		})
		entry.out = out
		if out.err != nil && out.err.Retryable {
			x.mu.Lock()
			delete(x.seen, key)
			x.mu.Unlock()
		}
		close(entry.done)
		if runCtx.Err() != nil {
			return
		}
		x.end(ec, out, yield)
	}
}

func (x *executor) run(ctx context.Context, skill Skill, outputSchema string, sc *SkillContext, input map[string]any) outcome {
	fail := func(e *SkillError) outcome { return outcome{err: e} }
	res, err := skill.Run(ctx, sc, input)
	if err != nil {
		var se *SkillError
		var be *llm.BudgetExceededError
		switch {
		case errors.As(err, &se):
			return fail(se)
		case errors.As(err, &be):
			return fail(&SkillError{"BUDGET_EXCEEDED", be.Error(), false})
		}
		return fail(&SkillError{"INTERNAL", err.Error(), true})
	}
	if err := x.contracts.Validate(outputSchema, res.Output); err != nil {
		return fail(&SkillError{"INTERNAL", "invalid output: " + err.Error(), false})
	}
	if res.EventType != "" {
		if x.events == nil || res.EventData == nil {
			return fail(&SkillError{"INTERNAL", "completion event without event sink", false})
		}
		subject, _ := input["projectId"].(string)
		_, err := x.events.Publish(ctx, res.EventType, subject, res.EventData,
			events.PublishOptions{CorrelationID: sc.WorkflowID, CausationID: sc.MessageID})
		if err != nil {
			return fail(&SkillError{"DEPENDENCY_UNAVAILABLE", err.Error(), true})
		}
	}
	return outcome{output: res.Output}
}

func (x *executor) Cancel(ctx context.Context, ec *a2asrv.ExecutorContext) iter.Seq2[a2a.Event, error] {
	return func(yield func(a2a.Event, error) bool) {
		x.mu.Lock()
		cancel := x.running[ec.TaskID]
		x.mu.Unlock()
		if cancel != nil {
			cancel()
		}
		yield(a2a.NewStatusUpdateEvent(ec, a2a.TaskStateCanceled, nil), nil)
	}
}

type callerKey struct{}

// callerInterceptor copies the authenticated caller into the A2A call context.
type callerInterceptor struct {
	a2asrv.PassthroughCallInterceptor
}

func (callerInterceptor) Before(ctx context.Context, cc *a2asrv.CallContext, _ *a2asrv.Request) (context.Context, any, error) {
	caller, _ := ctx.Value(callerKey{}).(string)
	cc.User = a2asrv.NewAuthenticatedUser(caller, nil)
	return ctx, nil, nil
}

// Serve builds the HTTP handler: agent card, bearer-protected JSON-RPC on the card's first interface path, GET /v1/status.
func Serve(o Options) (http.Handler, error) {
	raw, err := os.ReadFile(o.AgentCardPath)
	if err != nil {
		return nil, err
	}
	var card a2a.AgentCard
	if err := json.Unmarshal(raw, &card); err != nil {
		return nil, fmt.Errorf("agent card %s: %w", o.AgentCardPath, err)
	}
	if len(card.SupportedInterfaces) == 0 {
		return nil, fmt.Errorf("agent card %s: no supportedInterfaces", o.AgentCardPath)
	}
	schemas := map[string]schemaRefs{}
	planned := map[string]bool{}
	for _, ext := range card.Capabilities.Extensions {
		if ext.URI != InvocationExtension {
			continue
		}
		if skills, ok := ext.Params["skills"].(map[string]any); ok {
			for id, v := range skills {
				m, _ := v.(map[string]any)
				in, _ := m["inputSchema"].(string)
				out, _ := m["outputSchema"].(string)
				schemas[id] = schemaRefs{in, out}
			}
		}
		switch p := ext.Params["plannedSkills"].(type) {
		case []any:
			for _, s := range p {
				if id, ok := s.(string); ok {
					planned[id] = true
				}
			}
		case map[string]any:
			for id := range p {
				planned[id] = true
			}
		}
	}
	skills := map[string]Skill{}
	for _, s := range o.Skills {
		if _, ok := schemas[s.ID]; !ok {
			return nil, fmt.Errorf("skill %s has no schema in %s", s.ID, o.AgentCardPath)
		}
		skills[s.ID] = s
	}
	c := o.Contracts
	if c == nil {
		if c, err = contracts.New(o.Settings.ContractsDir); err != nil {
			return nil, err
		}
	}
	tracker := o.Status
	if tracker == nil {
		tracker = status.NewTracker(o.Settings.ServiceName)
	}
	authenticate := auth.AuthenticatorFor(o.Settings.PluginInboundToken, o.Settings.ServiceVerify)
	exec := &executor{
		skills: skills, schemas: schemas, planned: planned, settings: o.Settings, contracts: c, events: o.Events,
		status: tracker, seen: map[string]*seenEntry{}, running: map[a2a.TaskID]context.CancelFunc{},
	}
	rpc := a2asrv.NewJSONRPCHandler(a2asrv.NewHandler(exec, a2asrv.WithCallInterceptors(callerInterceptor{})))
	u, err := url.Parse(card.SupportedInterfaces[0].URL)
	if err != nil {
		return nil, err
	}
	rpcPath := u.Path
	if rpcPath == "" {
		rpcPath = "/"
	}
	mux := http.NewServeMux()
	mux.Handle(a2asrv.WellKnownAgentCardPath, a2asrv.NewStaticAgentCardHandler(&card))
	mux.Handle("POST "+rpcPath, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		caller, ok := authenticate(r.Header.Get("Authorization"))
		if !ok {
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusUnauthorized)
			_ = json.NewEncoder(w).Encode(map[string]string{"error": "missing or invalid token"})
			return
		}
		rpc.ServeHTTP(w, r.WithContext(context.WithValue(r.Context(), callerKey{}, caller)))
	}))
	mux.Handle("GET /v1/status", status.Handler(tracker, authenticate, nil))
	return mux, nil
}
