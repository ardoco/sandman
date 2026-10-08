package edu.kit.kastel.mcse.ardoco.sandman.agent.a2a;

import static edu.kit.kastel.mcse.ardoco.sandman.agent.a2a.A2aServerTest.INPUT;
import static edu.kit.kastel.mcse.ardoco.sandman.agent.a2a.TestAgent.TOKENS;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import edu.kit.kastel.mcse.ardoco.sandman.agent.Telemetry;
import io.opentelemetry.api.OpenTelemetry;
import io.opentelemetry.api.trace.Span;
import io.opentelemetry.api.trace.propagation.W3CTraceContextPropagator;
import io.opentelemetry.context.Scope;
import io.opentelemetry.context.propagation.ContextPropagators;
import io.opentelemetry.sdk.OpenTelemetrySdk;
import io.opentelemetry.sdk.testing.exporter.InMemorySpanExporter;
import io.opentelemetry.sdk.trace.SdkTracerProvider;
import io.opentelemetry.sdk.trace.data.SpanData;
import io.opentelemetry.sdk.trace.export.SimpleSpanProcessor;
import io.quarkus.test.common.http.TestHTTPResource;
import io.quarkus.test.junit.QuarkusTest;
import jakarta.inject.Inject;
import java.net.URI;
import java.util.UUID;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

@QuarkusTest
class A2aTelemetryTest {

    final InMemorySpanExporter spans = InMemorySpanExporter.create();

    @TestHTTPResource("/")
    URI url;

    @Inject
    TestAgent.Recover skill;

    @Inject
    TestAgent.Events events;

    @BeforeEach
    void install() {
        skill.output = TestAgent.OUTPUT;
        skill.error = null;
        skill.started = null;
        skill.release = null;
        events.fail = false;
        Telemetry.use(OpenTelemetrySdk.builder()
                .setTracerProvider(SdkTracerProvider.builder()
                        .addSpanProcessor(SimpleSpanProcessor.create(spans))
                        .build())
                .setPropagators(ContextPropagators.create(W3CTraceContextPropagator.getInstance()))
                .build());
    }

    @AfterEach
    void uninstall() {
        Telemetry.use(OpenTelemetry.noop());
    }

    SpanData span(String prefix) {
        return spans.getFinishedSpanItems().stream()
                .filter(s -> s.getName().startsWith(prefix))
                .findFirst()
                .orElseThrow();
    }

    @Test
    void clientCallAndServerTaskShareOneTrace() throws Exception {
        try (A2aClient client = new A2aClient(TOKENS.get("orchestrator"))) {
            Span root = Telemetry.tracer().spanBuilder("root").startSpan();
            A2aClient.TaskSnapshot task;
            try (Scope ignored = root.makeCurrent()) {
                task = client.submit(
                        url.toString(),
                        "recover-trace-links",
                        INPUT,
                        null,
                        null,
                        UUID.randomUUID().toString());
                for (int i = 0; i < 500 && !task.terminal(); i++) {
                    Thread.sleep(10);
                    task = client.get(url.toString(), task.taskId());
                }
            } finally {
                root.end();
            }
            assertTrue(task.terminal());
            SpanData server = span("a2a.task");
            assertEquals(span("root").getTraceId(), server.getTraceId());
            assertEquals(span("a2a.client send").getSpanId(), server.getParentSpanId());
            assertEquals("recover-trace-links", server.getAttributes().get(Telemetry.ATTR_SKILL));
            assertEquals("tlr-agent", server.getAttributes().get(Telemetry.ATTR_AGENT));
            assertEquals("completed", server.getAttributes().get(Telemetry.ATTR_OUTCOME));
            assertEquals(task.taskId(), server.getAttributes().get(Telemetry.ATTR_TASK_ID));
        }
    }
}
