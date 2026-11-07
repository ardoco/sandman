// Package kg is the knowledge-graph client on neo4j-go-driver, with the Traverse quirks of docs/tasks/T-kg-traverse-migration.md.
package kg

import (
	"context"
	"errors"
	"os"
	"strings"
	"time"

	"github.com/ArDoCo/sandman/libs/go/sandman-agent/telemetry"
	"github.com/neo4j/neo4j-go-driver/v6/neo4j"
	"github.com/neo4j/neo4j-go-driver/v6/neo4j/auth"
)

const MemoryLimitCode = "Neo.TransientError.General.MemoryPoolOutOfMemoryError"

// MemoryLimitError is not retried. It deliberately has no Unwrap: the driver retries a wrapped transient Neo4jError.
type MemoryLimitError struct{ Msg string }

func (e *MemoryLimitError) Error() string { return "memory limit: " + e.Msg }

type Row = map[string]any

type Tx interface {
	Run(ctx context.Context, cypher string, params map[string]any) ([]Row, error)
}

type Session interface {
	ExecuteRead(ctx context.Context, work func(Tx) (any, error)) (any, error)
	ExecuteWrite(ctx context.Context, work func(Tx) (any, error)) (any, error)
	Run(ctx context.Context, cypher string, params map[string]any) ([]Row, error)
	Close(ctx context.Context) error
}

type Driver interface {
	Session(ctx context.Context) Session
	Close(ctx context.Context) error
}

func isMemoryLimit(err error) (string, bool) {
	var ne *neo4j.Neo4jError
	if errors.As(err, &ne) && ne.Code == MemoryLimitCode {
		return ne.Msg, true
	}
	return "", false
}

// noRetryOnMemoryLimit turns the memory-limit error into a plain error, which stops the driver retry loop.
func noRetryOnMemoryLimit(fn func(Tx) (any, error)) func(Tx) (any, error) {
	return func(tx Tx) (any, error) {
		out, err := fn(tx)
		if msg, ok := isMemoryLimit(err); ok {
			return nil, &MemoryLimitError{msg}
		}
		return out, err
	}
}

func Now() string { return time.Now().UTC().Format("2006-01-02T15:04:05Z") }

type Client struct{ driver Driver }

// New connects with the neo4j-go-driver. An empty user means no authentication.
func New(uri, user, password string) (*Client, error) {
	var manager auth.TokenManager
	if user != "" {
		manager = auth.BasicTokenManager(func(context.Context) (neo4j.AuthToken, error) {
			return neo4j.BasicAuth(user, password, ""), nil
		})
	}
	d, err := neo4j.NewDriver(uri, manager)
	if err != nil {
		return nil, err
	}
	return &Client{driver: neo4jDriver{d}}, nil
}

func NewWithDriver(d Driver) *Client { return &Client{d} }

func (c *Client) Close(ctx context.Context) error { return c.driver.Close(ctx) }

func (c *Client) inSession(ctx context.Context, op, cypher string, fn func(context.Context, Session) ([]Row, error)) ([]Row, error) {
	if len(cypher) > 200 {
		cypher = cypher[:200]
	}
	var rows []Row
	err := telemetry.WithSpan(ctx, "sandman.kg."+op, map[string]string{"db.statement": cypher}, "", func(ctx context.Context) error {
		s := c.driver.Session(ctx)
		defer s.Close(ctx)
		var err error
		rows, err = fn(ctx, s)
		return err
	})
	return rows, err
}

func rowsOf(out any, err error) ([]Row, error) {
	if err != nil {
		return nil, err
	}
	rows, _ := out.([]Row)
	return rows, nil
}

// Read runs in a read transaction. Traverse rejects the words drop, set, delete, create, merge and remove in read
// query text, also in string literals: pass them as parameters. A query whose rows enter an LLM prompt ends in
// ORDER BY on a stable key.
func (c *Client) Read(ctx context.Context, cypher string, params map[string]any) ([]Row, error) {
	return c.inSession(ctx, "read", cypher, func(ctx context.Context, s Session) ([]Row, error) {
		return rowsOf(s.ExecuteRead(ctx, noRetryOnMemoryLimit(func(tx Tx) (any, error) { return tx.Run(ctx, cypher, params) })))
	})
}

func (c *Client) Write(ctx context.Context, cypher string, params map[string]any) ([]Row, error) {
	return c.inSession(ctx, "write", cypher, func(ctx context.Context, s Session) ([]Row, error) {
		return rowsOf(s.ExecuteWrite(ctx, noRetryOnMemoryLimit(func(tx Tx) (any, error) { return tx.Run(ctx, cypher, params) })))
	})
}

// WriteTx runs fn in one write transaction. fn may run more than once on transient errors.
func (c *Client) WriteTx(ctx context.Context, fn func(Tx) error) error {
	_, err := c.inSession(ctx, "write_tx", "", func(ctx context.Context, s Session) ([]Row, error) {
		_, err := s.ExecuteWrite(ctx, noRetryOnMemoryLimit(func(tx Tx) (any, error) { return nil, fn(tx) }))
		return nil, err
	})
	return err
}

func (c *Client) RunAutocommit(ctx context.Context, cypher string, params map[string]any) ([]Row, error) {
	return c.inSession(ctx, "autocommit", cypher, func(ctx context.Context, s Session) ([]Row, error) {
		return s.Run(ctx, cypher, params)
	})
}

// InitSchema runs the statements of a cypher file. CREATE INDEX has no IF NOT EXISTS in Traverse: an "already exists"
// error is ignored.
func (c *Client) InitSchema(ctx context.Context, cypherFile string) error {
	raw, err := os.ReadFile(cypherFile)
	if err != nil {
		return err
	}
	var lines []string
	for _, line := range strings.Split(string(raw), "\n") {
		if !strings.HasPrefix(strings.TrimSpace(line), "//") {
			lines = append(lines, line)
		}
	}
	for _, statement := range strings.Split(strings.Join(lines, "\n"), ";") {
		statement = strings.TrimSpace(statement)
		if statement == "" {
			continue
		}
		if _, err := c.RunAutocommit(ctx, statement, nil); err != nil && !strings.Contains(err.Error(), "already exists") {
			return err
		}
	}
	return nil
}
