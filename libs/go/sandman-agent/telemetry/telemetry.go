// Package telemetry wraps OpenTelemetry spans and trace propagation.
package telemetry

import (
	"context"

	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/codes"
	"go.opentelemetry.io/otel/propagation"
)

var propagator = propagation.TraceContext{}

// WithSpan runs fn in a span. A non-empty parent (W3C traceparent) becomes the span's remote parent.
func WithSpan(ctx context.Context, name string, attrs map[string]string, parent string, fn func(context.Context) error) error {
	if parent != "" {
		ctx = propagator.Extract(ctx, propagation.MapCarrier{"traceparent": parent})
	}
	kv := make([]attribute.KeyValue, 0, len(attrs))
	for k, v := range attrs {
		kv = append(kv, attribute.String(k, v))
	}
	ctx, span := otel.Tracer("sandman-agent").Start(ctx, name)
	defer span.End()
	span.SetAttributes(kv...)
	err := fn(ctx)
	if err != nil {
		span.SetStatus(codes.Error, err.Error())
	}
	return err
}

// Traceparent returns the W3C traceparent of ctx, or an empty string without an active span.
func Traceparent(ctx context.Context) string {
	carrier := propagation.MapCarrier{}
	propagator.Inject(ctx, carrier)
	return carrier["traceparent"]
}
