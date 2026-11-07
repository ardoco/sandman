package kg

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"

	"github.com/neo4j/neo4j-go-driver/v6/neo4j"
)

type fakeDriver struct {
	modes      []string
	statements []string
	params     []map[string]any
	attempts   int
	fail       func(cypher string) error
}

func (d *fakeDriver) Close(context.Context) error     { return nil }
func (d *fakeDriver) Session(context.Context) Session { return &fakeSession{d} }

type fakeSession struct{ d *fakeDriver }

func (s *fakeSession) Close(context.Context) error { return nil }
func (s *fakeSession) run(cypher string, params map[string]any) ([]Row, error) {
	s.d.attempts++
	s.d.statements = append(s.d.statements, cypher)
	s.d.params = append(s.d.params, params)
	if s.d.fail != nil {
		if err := s.d.fail(cypher); err != nil {
			return nil, err
		}
	}
	return []Row{{"n": int64(1)}}, nil
}
func (s *fakeSession) Run(_ context.Context, c string, p map[string]any) ([]Row, error) {
	s.d.modes = append(s.d.modes, "auto")
	return s.run(c, p)
}

type fakeTx struct{ s *fakeSession }

func (t fakeTx) Run(_ context.Context, c string, p map[string]any) ([]Row, error) {
	return t.s.run(c, p)
}

// like the real driver: transient Neo4j errors are retried up to 3 times
func (s *fakeSession) exec(mode string, work func(Tx) (any, error)) (any, error) {
	s.d.modes = append(s.d.modes, mode)
	for i := 1; ; i++ {
		out, err := work(fakeTx{s})
		var ne *neo4j.Neo4jError
		if err == nil || i >= 3 || !errors.As(err, &ne) || !strings.HasPrefix(ne.Code, "Neo.TransientError") {
			return out, err
		}
	}
}
func (s *fakeSession) ExecuteRead(_ context.Context, w func(Tx) (any, error)) (any, error) {
	return s.exec("read", w)
}
func (s *fakeSession) ExecuteWrite(_ context.Context, w func(Tx) (any, error)) (any, error) {
	return s.exec("write", w)
}

func neoErr(code, msg string) error { return &neo4j.Neo4jError{Code: code, Msg: msg} }

var ctx = context.Background()

func TestReadUsesReadTransaction(t *testing.T) {
	d := &fakeDriver{}
	rows, err := NewWithDriver(d).Read(ctx, "MATCH (n) RETURN n", nil)
	if err != nil || len(rows) != 1 || rows[0]["n"] != int64(1) || len(d.modes) != 1 || d.modes[0] != "read" {
		t.Fatal(rows, err, d.modes)
	}
}

func TestMemoryLimitIsNotRetried(t *testing.T) {
	d := &fakeDriver{fail: func(string) error { return neoErr(MemoryLimitCode, "limit") }}
	_, err := NewWithDriver(d).Read(ctx, "MATCH (n) RETURN n", nil)
	var ml *MemoryLimitError
	if !errors.As(err, &ml) || d.attempts != 1 {
		t.Fatal(err, d.attempts)
	}
	d = &fakeDriver{fail: func(string) error { return neoErr(MemoryLimitCode, "limit") }}
	err = NewWithDriver(d).WriteTx(ctx, func(tx Tx) error { _, err := tx.Run(ctx, "X", nil); return err })
	if !errors.As(err, &ml) || d.attempts != 1 {
		t.Fatal(err, d.attempts)
	}
}

func TestOtherTransientErrorsAreRetried(t *testing.T) {
	d := &fakeDriver{fail: func(string) error { return neoErr("Neo.TransientError.Transaction.DeadlockDetected", "deadlock") }}
	_, err := NewWithDriver(d).Write(ctx, "MATCH (n) RETURN n", nil)
	if err == nil || !strings.Contains(err.Error(), "deadlock") || d.attempts != 3 {
		t.Fatal(err, d.attempts)
	}
}

func TestInitSchemaSkipsCommentsAndIgnoresAlreadyExists(t *testing.T) {
	d := &fakeDriver{fail: func(c string) error {
		if strings.Contains(c, "idx_a") {
			return errors.New("Index already exists")
		}
		return nil
	}}
	file := filepath.Join(t.TempDir(), "init.cypher")
	_ = os.WriteFile(file, []byte("// comment\nCREATE INDEX idx_a FOR (n:A) ON (n.id);\nCREATE INDEX idx_b FOR (n:B) ON (n.id);\n"), 0o600)
	c := NewWithDriver(d)
	if err := c.InitSchema(ctx, file); err != nil {
		t.Fatal(err)
	}
	if len(d.statements) != 2 || !strings.HasPrefix(d.statements[0], "CREATE INDEX idx_a") || !strings.HasPrefix(d.statements[1], "CREATE INDEX idx_b") {
		t.Fatal(d.statements)
	}
	d.fail = func(string) error { return errors.New("syntax error") }
	if err := c.InitSchema(ctx, file); err == nil || !strings.Contains(err.Error(), "syntax error") {
		t.Fatal(err)
	}
}

func TestRunRecorder(t *testing.T) {
	d := &fakeDriver{}
	r := &RunRecorder{KG: NewWithDriver(d), Agent: "tlr-agent", AgentVersion: "1"}
	if err := r.Complete(ctx, "run_x", &Usage{7, 3}, "", "", false); err != nil {
		t.Fatal(err)
	}
	if p := d.params[0]; p["inp"] != int64(7) || p["out"] != int64(3) || p["status"] != "completed" || p["mixed"] != nil {
		t.Fatal(p)
	}
	id, err := r.Start(ctx, RunStart{ProjectID: "p", Skill: "s", Tool: "t", ToolVersion: "1", InputKey: "k", ReplayMode: "live"})
	if err != nil || !regexp.MustCompile(`^run_[0-9a-f]{32}$`).MatchString(id) {
		t.Fatal(id, err)
	}
	props := d.params[1]["props"].(map[string]any)
	if _, has := props["model"]; has || props["id"] != id || props["status"] != "running" || props["replayMode"] != "live" {
		t.Fatal(props)
	}
}
