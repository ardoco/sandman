package edu.kit.kastel.mcse.ardoco.sandman.agent;

import io.opentelemetry.api.GlobalOpenTelemetry;
import io.opentelemetry.api.OpenTelemetry;
import io.opentelemetry.api.common.AttributeKey;
import io.opentelemetry.api.common.Attributes;
import io.opentelemetry.api.metrics.DoubleHistogram;
import io.opentelemetry.api.metrics.LongCounter;
import io.opentelemetry.api.trace.Span;
import io.opentelemetry.api.trace.SpanBuilder;
import io.opentelemetry.api.trace.SpanKind;
import io.opentelemetry.api.trace.StatusCode;
import io.opentelemetry.api.trace.Tracer;
import io.opentelemetry.context.Context;
import io.opentelemetry.context.Scope;
import io.opentelemetry.context.propagation.TextMapGetter;
import io.opentelemetry.context.propagation.TextMapSetter;
import java.util.HashMap;
import java.util.Map;

/**
 * Spans, metrics and W3C propagation of the agent libraries (opentelemetry-api only). Without an SDK (telemetry
 * disabled) every call is a no-op. Names mirror the Python library (sandman_agent.telemetry).
 */
public final class Telemetry {

    public static final String INSTRUMENTATION = "sandman-agent-lib";

    public static final String SPAN_A2A_TASK = "a2a.task";
    public static final String SPAN_A2A_CLIENT = "a2a.client";
    public static final String SPAN_EVENT_PUBLISH = "event.publish";
    public static final String SPAN_EVENT_CONSUME = "event.consume";
    public static final String SPAN_KG = "kg";
    public static final String SPAN_PLUGIN_DETECT = "plugin.detect";

    public static final AttributeKey<String> ATTR_AGENT = AttributeKey.stringKey("sandman.agent");
    public static final AttributeKey<String> ATTR_SKILL = AttributeKey.stringKey("sandman.skill");
    public static final AttributeKey<String> ATTR_TASK_ID = AttributeKey.stringKey("sandman.task.id");
    public static final AttributeKey<String> ATTR_OUTCOME = AttributeKey.stringKey("sandman.outcome");
    public static final AttributeKey<String> ATTR_EVENT_TYPE = AttributeKey.stringKey("sandman.event.type");
    public static final AttributeKey<String> ATTR_EVENT_ID = AttributeKey.stringKey("sandman.event.id");
    public static final AttributeKey<String> ATTR_MESSAGING_SYSTEM = AttributeKey.stringKey("messaging.system");
    public static final AttributeKey<String> ATTR_DB_SYSTEM = AttributeKey.stringKey("db.system.name");
    public static final AttributeKey<String> ATTR_DB_STATEMENT = AttributeKey.stringKey("db.query.text");
    public static final AttributeKey<String> ATTR_DB_OPERATION = AttributeKey.stringKey("db.operation.name");

    public static final String METRIC_A2A_TASK_DURATION = "sandman.a2a.task.duration";
    public static final String METRIC_EVENTS_PUBLISHED = "sandman.events.published";
    public static final String METRIC_EVENTS_CONSUMED = "sandman.events.consumed";
    public static final String METRIC_EVENT_CONSUME_DURATION = "sandman.events.consume.duration";

    /** Body of a span; may throw a checked exception of type E. */
    @FunctionalInterface
    public interface Body<T, E extends Exception> {
        T run() throws E;
    }

    private record Instruments(
            Tracer tracer,
            OpenTelemetry otel,
            DoubleHistogram a2aDuration,
            LongCounter published,
            LongCounter consumed,
            DoubleHistogram consumeDuration) {

        static Instruments of(OpenTelemetry otel) {
            var meter = otel.getMeter(INSTRUMENTATION);
            return new Instruments(
                    otel.getTracer(INSTRUMENTATION),
                    otel,
                    meter.histogramBuilder(METRIC_A2A_TASK_DURATION)
                            .setUnit("s")
                            .build(),
                    meter.counterBuilder(METRIC_EVENTS_PUBLISHED).build(),
                    meter.counterBuilder(METRIC_EVENTS_CONSUMED).build(),
                    meter.histogramBuilder(METRIC_EVENT_CONSUME_DURATION)
                            .setUnit("s")
                            .build());
        }
    }

    private static volatile Instruments instruments;

    private Telemetry() {}

    /** Uses this OpenTelemetry instance (the Quarkus bean or a test SDK) instead of the global one. */
    public static void use(OpenTelemetry otel) {
        instruments = Instruments.of(otel);
    }

    private static Instruments in() {
        Instruments i = instruments;
        if (i == null) {
            i = Instruments.of(GlobalOpenTelemetry.get());
            instruments = i;
        }
        return i;
    }

    public static Tracer tracer() {
        return in().tracer();
    }

    private static final TextMapSetter<Map<String, String>> SETTER = (carrier, key, value) -> {
        if (carrier != null) {
            carrier.put(key, value);
        }
    };

    private static final TextMapGetter<Map<String, String>> GETTER = new TextMapGetter<>() {
        @Override
        public Iterable<String> keys(Map<String, String> carrier) {
            return carrier.keySet();
        }

        @Override
        public String get(Map<String, String> carrier, String key) {
            return carrier == null ? null : carrier.get(key);
        }
    };

    /** Adds the W3C trace headers of the current context to the carrier. */
    public static void inject(Map<String, String> carrier) {
        in().otel().getPropagators().getTextMapPropagator().inject(Context.current(), carrier, SETTER);
    }

    /** W3C traceparent of the current span, or null without a valid span. */
    public static String traceparent() {
        Map<String, String> carrier = new HashMap<>();
        inject(carrier);
        return carrier.get("traceparent");
    }

    /** Parent context from W3C headers; keys are matched case-insensitively. */
    public static Context extract(Map<?, ?> headers) {
        Map<String, String> lower = new HashMap<>();
        if (headers != null) {
            headers.forEach(
                    (k, v) -> lower.put(String.valueOf(k).toLowerCase(java.util.Locale.ROOT), String.valueOf(v)));
        }
        return in().otel().getPropagators().getTextMapPropagator().extract(Context.root(), lower, GETTER);
    }

    /** Parent context of an event envelope's traceparent (root context if null). */
    public static Context fromTraceparent(String traceparent) {
        return traceparent == null ? Context.current() : extract(Map.of("traceparent", traceparent));
    }

    /** Runs the body in a span made current; failures set the error status and are rethrown. */
    public static <T, E extends Exception> T inSpan(SpanBuilder builder, Body<T, E> body) throws E {
        Span span = builder.startSpan();
        try (Scope ignored = span.makeCurrent()) {
            return body.run();
        } catch (Exception e) {
            span.recordException(e);
            span.setStatus(StatusCode.ERROR, e.getClass().getSimpleName());
            throw e;
        } finally {
            span.end();
        }
    }

    public static SpanBuilder span(String name, SpanKind kind, Attributes attributes) {
        return tracer().spanBuilder(name).setSpanKind(kind).setAllAttributes(attributes);
    }

    /** Memgraph span; the statement text only, never parameters. */
    public static SpanBuilder kgSpan(String operation, String statement) {
        var attrs = Attributes.builder().put(ATTR_DB_SYSTEM, "memgraph").put(ATTR_DB_OPERATION, operation);
        if (statement != null) {
            attrs.put(ATTR_DB_STATEMENT, statement);
        }
        return span(SPAN_KG + " " + operation, SpanKind.CLIENT, attrs.build());
    }

    public static SpanBuilder publishSpan(String eventType) {
        return span(
                SPAN_EVENT_PUBLISH + " " + eventType,
                SpanKind.PRODUCER,
                Attributes.of(ATTR_MESSAGING_SYSTEM, "nats", ATTR_EVENT_TYPE, eventType));
    }

    public static void eventPublished(String eventType) {
        in().published().add(1, Attributes.of(ATTR_EVENT_TYPE, eventType));
    }

    /** Consumer span continuing the producer's trace; counts and times the handler. */
    public static <E extends Exception> void consume(Event event, Body<Void, E> handler) throws E {
        long start = System.nanoTime();
        String outcome = "ok";
        try {
            inSpan(
                    span(
                                    SPAN_EVENT_CONSUME + " " + event.type(),
                                    SpanKind.CONSUMER,
                                    Attributes.of(
                                            ATTR_MESSAGING_SYSTEM, "nats",
                                            ATTR_EVENT_TYPE, event.type(),
                                            ATTR_EVENT_ID, event.id()))
                            .setParent(fromTraceparent(event.traceparent())),
                    handler);
        } catch (Exception e) {
            outcome = "error";
            throw e;
        } finally {
            Attributes attrs = Attributes.of(ATTR_EVENT_TYPE, event.type(), ATTR_OUTCOME, outcome);
            in().consumed().add(1, attrs);
            in().consumeDuration().record((System.nanoTime() - start) / 1e9, attrs);
        }
    }

    /** Server span of one A2A task as child of the caller's trace; the body returns the outcome. */
    public static String a2aTask(
            String agent, String skill, String taskId, Map<?, ?> headers, Body<String, RuntimeException> body) {
        long start = System.nanoTime();
        Span span = span(
                        SPAN_A2A_TASK + " " + skill,
                        SpanKind.SERVER,
                        Attributes.of(ATTR_AGENT, agent, ATTR_SKILL, skill, ATTR_TASK_ID, taskId))
                .setParent(extract(headers))
                .startSpan();
        String outcome = "failed";
        try (Scope ignored = span.makeCurrent()) {
            outcome = body.run();
            return outcome;
        } catch (RuntimeException e) {
            span.recordException(e);
            throw e;
        } finally {
            span.setAttribute(ATTR_OUTCOME, outcome);
            if (!"completed".equals(outcome)) {
                span.setStatus(StatusCode.ERROR, outcome);
            }
            in().a2aDuration()
                    .record(
                            (System.nanoTime() - start) / 1e9,
                            Attributes.of(ATTR_AGENT, agent, ATTR_SKILL, skill, ATTR_OUTCOME, outcome));
            span.end();
        }
    }
}
