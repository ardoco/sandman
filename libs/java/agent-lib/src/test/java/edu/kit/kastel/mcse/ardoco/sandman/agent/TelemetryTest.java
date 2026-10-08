package edu.kit.kastel.mcse.ardoco.sandman.agent;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

import io.nats.client.impl.Headers;
import io.opentelemetry.api.OpenTelemetry;
import io.opentelemetry.api.trace.Span;
import io.opentelemetry.api.trace.propagation.W3CTraceContextPropagator;
import io.opentelemetry.context.Scope;
import io.opentelemetry.context.propagation.ContextPropagators;
import io.opentelemetry.sdk.OpenTelemetrySdk;
import io.opentelemetry.sdk.metrics.SdkMeterProvider;
import io.opentelemetry.sdk.testing.exporter.InMemoryMetricReader;
import io.opentelemetry.sdk.testing.exporter.InMemorySpanExporter;
import io.opentelemetry.sdk.trace.SdkTracerProvider;
import io.opentelemetry.sdk.trace.data.SpanData;
import io.opentelemetry.sdk.trace.export.SimpleSpanProcessor;
import java.lang.reflect.Proxy;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.neo4j.driver.Driver;
import org.neo4j.driver.Session;
import org.neo4j.driver.TransactionCallback;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

class TelemetryTest {

    static final InMemorySpanExporter SPANS = InMemorySpanExporter.create();
    static final InMemoryMetricReader METRICS = InMemoryMetricReader.create();

    @BeforeEach
    void install() {
        SPANS.reset();
        Telemetry.use(OpenTelemetrySdk.builder()
                .setTracerProvider(SdkTracerProvider.builder()
                        .addSpanProcessor(SimpleSpanProcessor.create(SPANS))
                        .build())
                .setMeterProvider(
                        SdkMeterProvider.builder().registerMetricReader(METRICS).build())
                .setPropagators(ContextPropagators.create(W3CTraceContextPropagator.getInstance()))
                .build());
    }

    @AfterAll
    static void uninstall() {
        Telemetry.use(OpenTelemetry.noop());
    }

    static SpanData span(String namePrefix) {
        return SPANS.getFinishedSpanItems().stream()
                .filter(s -> s.getName().startsWith(namePrefix))
                .findFirst()
                .orElseThrow();
    }

    @Test
    void eventCarriesTraceparentFromPublishToConsume() {
        var ids = new ArrayList<String>();
        var payloads = new ArrayList<byte[]>();
        var js = (io.nats.client.JetStream) Proxy.newProxyInstance(
                io.nats.client.JetStream.class.getClassLoader(),
                new Class<?>[] {io.nats.client.JetStream.class},
                (proxy, method, args) -> {
                    ids.add(((Headers) args[1]).getFirst("Nats-Msg-Id"));
                    payloads.add((byte[]) args[2]);
                    return null;
                });
        Span root = Telemetry.tracer().spanBuilder("root").startSpan();
        try (Scope ignored = root.makeCurrent()) {
            new EventPublisher(js, LibraryTest.CONTRACTS, "tlr-agent")
                    .publish("sandman.tracelinks.updated", "mini", EventsTest.DATA, null, "m-1", null);
        } finally {
            root.end();
        }
        JsonNode envelope = JsonMapper.shared().readTree(payloads.get(0));
        LibraryTest.CONTRACTS.validate("events.schema.json", envelope);
        assertTrue(envelope.get("traceparent").asString().matches("00-[0-9a-f]{32}-[0-9a-f]{16}-0[01]"));

        var calls = new ArrayList<String>();
        EventConsumer.handle(EventsTest.fakeMessage(payloads.get(0), calls), e -> {});
        assertEquals(List.of("ack"), calls);
        SpanData publish = span("event.publish");
        SpanData consume = span("event.consume");
        assertEquals(span("root").getTraceId(), publish.getTraceId());
        assertEquals(publish.getTraceId(), consume.getTraceId());
        assertEquals(publish.getSpanId(), consume.getParentSpanId());
        assertEquals("sandman.tracelinks.updated", consume.getAttributes().get(Telemetry.ATTR_EVENT_TYPE));
        assertFalse(METRICS.collectAllMetrics().stream()
                .filter(m -> m.getName().equals(Telemetry.METRIC_EVENTS_CONSUMED))
                .toList()
                .isEmpty());
    }

    @Test
    void eventWithoutActiveSpanHasNoTraceparentWhenDisabled() {
        Telemetry.use(OpenTelemetry.noop());
        Event event = Event.build(
                LibraryTest.CONTRACTS, "x", "sandman.tracelinks.updated", "mini", EventsTest.DATA, null, "m-1", null);
        assertNull(event.traceparent());
    }

    static <T> T stub(Class<T> type, java.util.function.BiFunction<String, Object[], Object> handler) {
        return type.cast(Proxy.newProxyInstance(
                type.getClassLoader(),
                new Class<?>[] {type},
                (proxy, method, args) -> handler.apply(method.getName(), args)));
    }

    @Test
    void kgSpansCarryStatementButNoParameters() {
        var result = stub(org.neo4j.driver.Result.class, (m, a) -> List.of());
        var tx = stub(org.neo4j.driver.TransactionContext.class, (m, a) -> result);
        var session = stub(Session.class, (m, a) -> switch (m) {
            case "executeRead", "executeWrite" -> {
                try {
                    yield ((TransactionCallback<?>) a[0]).execute(tx);
                } catch (Exception e) {
                    throw new IllegalStateException(e);
                }
            }
            default -> null;
        });
        KgClient kg = new KgClient(stub(Driver.class, (m, a) -> session));
        kg.read("MATCH (n {id: $id}) RETURN n", Map.of("id", "SECRET-VALUE"));
        kg.write("CREATE (n {id: $id})", Map.of("id", "SECRET-VALUE"));
        kg.writeTx(t -> "done");
        SpanData read = span("kg read");
        assertEquals("memgraph", read.getAttributes().get(Telemetry.ATTR_DB_SYSTEM));
        assertEquals("MATCH (n {id: $id}) RETURN n", read.getAttributes().get(Telemetry.ATTR_DB_STATEMENT));
        assertEquals("CREATE (n {id: $id})", span("kg write").getAttributes().get(Telemetry.ATTR_DB_STATEMENT));
        assertNull(span("kg write_tx").getAttributes().get(Telemetry.ATTR_DB_STATEMENT));
        assertFalse(SPANS.getFinishedSpanItems().toString().contains("SECRET"));
    }
}
