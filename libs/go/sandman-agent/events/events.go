// Package events builds CloudEvents envelopes and publishes them to JetStream.
package events

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"time"

	"github.com/ArDoCo/sandman/libs/go/sandman-agent/contracts"
	"github.com/ArDoCo/sandman/libs/go/sandman-agent/ids"
	"github.com/ArDoCo/sandman/libs/go/sandman-agent/telemetry"
	"github.com/nats-io/nats.go"
	"github.com/nats-io/nats.go/jetstream"
)

const Stream = "SANDMAN"

var ConsumerBackoff = []time.Duration{5 * time.Second, 30 * time.Second, 120 * time.Second, 300 * time.Second}

var StreamConfig = jetstream.StreamConfig{
	Name: Stream, Subjects: []string{"sandman.>"}, Storage: jetstream.FileStorage, Retention: jetstream.LimitsPolicy,
	MaxAge: 90 * 24 * time.Hour, Duplicates: 120 * time.Second, Discard: jetstream.DiscardOld, Replicas: 1,
}

type PublishError struct {
	Type, ID string
	Attempts int
	Err      error
}

func (e *PublishError) Error() string {
	return fmt.Sprintf("publishing %s %s failed after %d attempts: %v", e.Type, e.ID, e.Attempts, e.Err)
}
func (e *PublishError) Unwrap() error { return e.Err }

type Event struct {
	ID            string         `json:"id"`
	Type          string         `json:"type"`
	Source        string         `json:"source"`
	Subject       string         `json:"subject"`
	Time          string         `json:"time"`
	Data          map[string]any `json:"data"`
	CorrelationID string         `json:"-"`
	CausationID   string         `json:"-"`
	Traceparent   string         `json:"-"`
}

type PublishOptions struct{ CorrelationID, CausationID, EventID string }

// Sink is the publishing surface skills use. EventPublisher implements it.
type Sink interface {
	Publish(ctx context.Context, typ, subject string, data map[string]any, opts PublishOptions) (*Event, error)
}

func (e *Event) ToJSON() map[string]any {
	m := map[string]any{
		"specversion": "1.0", "id": e.ID, "source": e.Source, "type": e.Type, "subject": e.Subject, "time": e.Time,
		"datacontenttype": "application/json", "data": e.Data,
	}
	for k, v := range map[string]string{"correlationid": e.CorrelationID, "causationid": e.CausationID, "traceparent": e.Traceparent} {
		if v != "" {
			m[k] = v
		}
	}
	return m
}

func FromJSON(envelope map[string]any) (*Event, error) {
	text := func(k string) (string, error) {
		v, ok := envelope[k].(string)
		if !ok {
			return "", fmt.Errorf("event member %s missing", k)
		}
		return v, nil
	}
	opt := func(k string) string { v, _ := envelope[k].(string); return v }
	e := &Event{CorrelationID: opt("correlationid"), CausationID: opt("causationid"), Traceparent: opt("traceparent")}
	for k, dst := range map[string]*string{"id": &e.ID, "type": &e.Type, "source": &e.Source, "subject": &e.Subject, "time": &e.Time} {
		v, err := text(k)
		if err != nil {
			return nil, err
		}
		*dst = v
	}
	e.Data, _ = envelope["data"].(map[string]any)
	if e.Data == nil {
		e.Data = map[string]any{}
	}
	return e, nil
}

func BuildEvent(ctx context.Context, c *contracts.Contracts, source, typ, subject string, data map[string]any, o PublishOptions) (*Event, error) {
	e := &Event{
		ID: o.EventID, Type: typ, Source: "sandman/" + source, Subject: subject, Data: data,
		Time: time.Now().UTC().Format("2006-01-02T15:04:05Z"), CorrelationID: o.CorrelationID, CausationID: o.CausationID,
		Traceparent: telemetry.Traceparent(ctx),
	}
	if e.ID == "" {
		e.ID = ids.NewUUID()
	}
	return e, c.Validate("events.schema.json", e.ToJSON())
}

// Publisher is the part of jetstream.JetStream the publisher needs.
type Publisher interface {
	PublishMsg(ctx context.Context, msg *nats.Msg, opts ...jetstream.PublishOpt) (*jetstream.PubAck, error)
}

type EventPublisher struct {
	JS        Publisher
	Contracts *contracts.Contracts
	Source    string
	Backoff   []time.Duration
}

func NewPublisher(js Publisher, c *contracts.Contracts, source string) *EventPublisher {
	return &EventPublisher{JS: js, Contracts: c, Source: source, Backoff: []time.Duration{500 * time.Millisecond, 2 * time.Second, 5 * time.Second}}
}

// Publish validates the envelope, then publishes with Nats-Msg-Id = event id and retries with backoff.
func (p *EventPublisher) Publish(ctx context.Context, typ, subject string, data map[string]any, o PublishOptions) (*Event, error) {
	var event *Event
	err := telemetry.WithSpan(ctx, "sandman.event.publish", map[string]string{"sandman.event.type": typ}, "", func(ctx context.Context) error {
		var err error
		if event, err = BuildEvent(ctx, p.Contracts, p.Source, typ, subject, data, o); err != nil {
			return err
		}
		payload, err := json.Marshal(event.ToJSON())
		if err != nil {
			return err
		}
		for attempt := 0; ; attempt++ {
			msg := &nats.Msg{Subject: typ, Data: payload, Header: nats.Header{nats.MsgIdHdr: []string{event.ID}}}
			_, err = p.JS.PublishMsg(ctx, msg)
			if err == nil {
				return nil
			}
			if attempt >= len(p.Backoff) {
				return &PublishError{Type: typ, ID: event.ID, Attempts: attempt + 1, Err: err}
			}
			select {
			case <-time.After(p.Backoff[attempt]):
			case <-ctx.Done():
				return ctx.Err()
			}
		}
	})
	return event, err
}

type Handle struct {
	NC *nats.Conn
	JS jetstream.JetStream
}

func Connect(url string) (*Handle, error) {
	nc, err := nats.Connect(url)
	if err != nil {
		return nil, err
	}
	js, err := jetstream.New(nc)
	if err != nil {
		nc.Close()
		return nil, err
	}
	return &Handle{nc, js}, nil
}

func (h *Handle) Close() { h.NC.Close() }

func EnsureStream(ctx context.Context, js jetstream.JetStream) error {
	_, err := js.CreateOrUpdateStream(ctx, StreamConfig)
	return err
}

func ConsumerConfig(durable, subject string) jetstream.ConsumerConfig {
	if subject == "" {
		subject = "sandman.>"
	}
	return jetstream.ConsumerConfig{
		Durable: durable, FilterSubject: subject, AckPolicy: jetstream.AckExplicitPolicy, AckWait: 60 * time.Second,
		MaxDeliver: 5, BackOff: ConsumerBackoff, MaxAckPending: 100,
	}
}

type Handler func(ctx context.Context, e *Event) error

// Msg is the part of jetstream.Msg the consumer needs.
type Msg interface {
	Data() []byte
	Ack() error
	NakWithDelay(delay time.Duration) error
	Metadata() (*jetstream.MsgMetadata, error)
}

// HandleMessage acks after the handler succeeds. On failure it naks with the backoff of the delivery count.
func HandleMessage(ctx context.Context, msg Msg, handler Handler) {
	err := func() error {
		var envelope map[string]any
		if err := json.Unmarshal(msg.Data(), &envelope); err != nil {
			return err
		}
		event, err := FromJSON(envelope)
		if err != nil {
			return err
		}
		return telemetry.WithSpan(ctx, "sandman.event.consume", map[string]string{"sandman.event.type": event.Type, "sandman.event.id": event.ID},
			event.Traceparent, func(ctx context.Context) error { return handler(ctx, event) })
	}()
	if err != nil {
		slog.Error("event handler failed", "error", err)
		delivered := 1
		if md, mdErr := msg.Metadata(); mdErr == nil && md.NumDelivered > 0 {
			delivered = int(md.NumDelivered)
		}
		_ = msg.NakWithDelay(ConsumerBackoff[min(delivered, len(ConsumerBackoff))-1])
		return
	}
	_ = msg.Ack()
}

// RunConsumer fetches batches until ctx ends.
func RunConsumer(ctx context.Context, h *Handle, durable, subject string, handler Handler) error {
	consumer, err := h.JS.CreateOrUpdateConsumer(ctx, Stream, ConsumerConfig(durable, subject))
	if err != nil {
		return err
	}
	for ctx.Err() == nil {
		batch, err := consumer.Fetch(10, jetstream.FetchMaxWait(2*time.Second))
		if err != nil {
			return err
		}
		for msg := range batch.Messages() {
			HandleMessage(ctx, msg, handler)
		}
	}
	return nil
}
