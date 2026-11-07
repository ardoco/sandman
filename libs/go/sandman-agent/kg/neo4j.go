package kg

import (
	"context"

	"github.com/neo4j/neo4j-go-driver/v6/neo4j"
)

type neo4jDriver struct{ d neo4j.Driver }

func (n neo4jDriver) Session(ctx context.Context) Session {
	return neo4jSession{n.d.NewSession(ctx, neo4j.SessionConfig{})}
}
func (n neo4jDriver) Close(ctx context.Context) error { return n.d.Close(ctx) }

type neo4jSession struct{ s neo4j.Session }

func collect(ctx context.Context, res neo4j.Result, err error) ([]Row, error) {
	recs, err := neo4j.Collect(ctx, res, err)
	if err != nil {
		return nil, err
	}
	rows := make([]Row, len(recs))
	for i, r := range recs {
		rows[i] = r.AsMap()
	}
	return rows, nil
}

type neo4jTx struct{ tx neo4j.ManagedTransaction }

func (t neo4jTx) Run(ctx context.Context, cypher string, params map[string]any) ([]Row, error) {
	res, err := t.tx.Run(ctx, cypher, params)
	return collect(ctx, res, err)
}

func wrap(work func(Tx) (any, error)) neo4j.ManagedTransactionWork {
	return func(tx neo4j.ManagedTransaction) (any, error) { return work(neo4jTx{tx}) }
}

func (s neo4jSession) ExecuteRead(ctx context.Context, work func(Tx) (any, error)) (any, error) {
	return s.s.ExecuteRead(ctx, wrap(work))
}
func (s neo4jSession) ExecuteWrite(ctx context.Context, work func(Tx) (any, error)) (any, error) {
	return s.s.ExecuteWrite(ctx, wrap(work))
}
func (s neo4jSession) Run(ctx context.Context, cypher string, params map[string]any) ([]Row, error) {
	res, err := s.s.Run(ctx, cypher, params)
	return collect(ctx, res, err)
}
func (s neo4jSession) Close(ctx context.Context) error { return s.s.Close(ctx) }
