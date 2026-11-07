package events

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"reflect"
	"testing"
	"time"

	"github.com/ArDoCo/sandman/libs/go/sandman-agent/contracts"
	"github.com/nats-io/nats.go"
	"github.com/nats-io/nats.go/jetstream"
)

const contractsDir = "../../../../contracts"

var validData = map[string]any{
	"projectId":        "mediastore",
	"sourceVersionIds": []any{"ver_0123456789abcdef0123456789abcdef"},
	"targetVersionIds": []any{"ver_fedcba9876543210fedcba9876543210"},
	"runs": []any{map[string]any{"approach": "lissa", "approachVersion": "1.0.0", "runId": "run_0123456789abcdef0123456789abcdef",
		"status": "completed", "linkCount": 3}},
}

func mustContracts(t *testing.T) *contracts.Contracts {
	c, err := contracts.New(contractsDir)
	if err != nil {
		t.Fatal(err)
	}
	return c
}

func TestEventEnvelopeVectors(t *testing.T) {
	raw, err := os.ReadFile("../../../../contracts/conformance/vectors.json")
	if err != nil {
		t.Fatal(err)
	}
	var v struct {
		EventEnvelope struct {
			Cases []struct {
				Valid bool
				Event map[string]any
			}
		}
	}
	if err := json.Unmarshal(raw, &v); err != nil {
		t.Fatal(err)
	}
	c := mustContracts(t)
	if len(v.EventEnvelope.Cases) == 0 {
		t.Fatal("no cases")
	}
	for i, tc := range v.EventEnvelope.Cases {
		if got := c.IsValid("events.schema.json", tc.Event); got != tc.Valid {
			t.Errorf("case %d: valid=%v want %v", i, got, tc.Valid)
		}
		if tc.Valid {
			e, err := FromJSON(tc.Event)
			if err != nil || !reflect.DeepEqual(e.ToJSON(), tc.Event) {
				t.Errorf("case %d: round trip differs: %v", i, err)
			}
		}
	}
}

type fakeJS struct {
	failures int
	ids      []string
	subjects []string
}

func (f *fakeJS) PublishMsg(_ context.Context, m *nats.Msg, _ ...jetstream.PublishOpt) (*jetstream.PubAck, error) {
	f.subjects = append(f.subjects, m.Subject)
	f.ids = append(f.ids, m.Header.Get(nats.MsgIdHdr))
	if f.failures > 0 {
		f.failures--
		return nil, errors.New("down")
	}
	return &jetstream.PubAck{}, nil
}

func TestPublishRetriesThenFails(t *testing.T) {
	c := mustContracts(t)
	js := &fakeJS{failures: 2}
	p := &EventPublisher{JS: js, Contracts: c, Source: "tlr-agent", Backoff: []time.Duration{0, 0, 0}}
	e, err := p.Publish(context.Background(), "sandman.tracelinks.updated", "mediastore", validData, PublishOptions{CausationID: "task-1"})
	if err != nil || len(js.subjects) != 3 || js.subjects[0] != "sandman.tracelinks.updated" || e.ID == "" || js.ids[0] != e.ID || js.ids[2] != e.ID {
		t.Fatal(err, js.subjects, js.ids)
	}
	down := &EventPublisher{JS: &fakeJS{failures: 99}, Contracts: c, Source: "tlr-agent", Backoff: []time.Duration{0, 0, 0}}
	_, err = down.Publish(context.Background(), "sandman.tracelinks.updated", "mediastore", validData, PublishOptions{CausationID: "task-1"})
	var pe *PublishError
	if !errors.As(err, &pe) || pe.Attempts != 4 {
		t.Fatal(err)
	}
}

func TestInvalidEnvelopeIsNotPublished(t *testing.T) {
	js := &fakeJS{}
	p := &EventPublisher{JS: js, Contracts: mustContracts(t), Source: "tlr-agent"}
	if _, err := p.Publish(context.Background(), "sandman.tracelinks.updated", "mediastore", map[string]any{"projectId": "mediastore"}, PublishOptions{}); err == nil {
		t.Fatal("invalid envelope accepted")
	}
	if len(js.subjects) != 0 {
		t.Fatal("published")
	}
}

type fakeMsg struct {
	data      []byte
	delivered uint64
	log       []string
}

func (m *fakeMsg) Data() []byte { return m.data }
func (m *fakeMsg) Ack() error   { m.log = append(m.log, "ack"); return nil }
func (m *fakeMsg) NakWithDelay(d time.Duration) error {
	m.log = append(m.log, "nak "+d.String())
	return nil
}
func (m *fakeMsg) Metadata() (*jetstream.MsgMetadata, error) {
	return &jetstream.MsgMetadata{NumDelivered: m.delivered}, nil
}

func TestAckAfterSuccessAndNakWithBackoff(t *testing.T) {
	data, _ := json.Marshal(map[string]any{
		"specversion": "1.0", "id": "0b6f4a0e-8f6e-4c6b-9d7a-1f2e3d4c5b6a", "source": "sandman/x", "type": "sandman.x",
		"subject": "p", "time": "2026-10-06T12:00:00Z", "datacontenttype": "application/json", "data": map[string]any{},
	})
	ok := &fakeMsg{data: data, delivered: 1}
	HandleMessage(context.Background(), ok, func(context.Context, *Event) error { return nil })
	if !reflect.DeepEqual(ok.log, []string{"ack"}) {
		t.Fatal(ok.log)
	}
	bad := &fakeMsg{data: data, delivered: 2}
	HandleMessage(context.Background(), bad, func(context.Context, *Event) error { return errors.New("boom") })
	if !reflect.DeepEqual(bad.log, []string{"nak " + ConsumerBackoff[1].String()}) {
		t.Fatal(bad.log)
	}
}
