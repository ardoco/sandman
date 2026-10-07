package edu.kit.kastel.mcse.ardoco.sandman.agent;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import io.nats.client.JetStream;
import io.nats.client.Message;
import io.nats.client.impl.Headers;
import java.io.IOException;
import java.lang.reflect.Proxy;
import java.time.Duration;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.concurrent.atomic.AtomicInteger;
import org.junit.jupiter.api.Test;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

class EventsTest {

    static final JsonNode DATA = JsonMapper.shared()
            .readTree("{\"projectId\": \"mini\", \"sourceVersionIds\": [], \"targetVersionIds\": [], \"runs\": []}");
    static final List<Duration> NO_WAIT = Collections.nCopies(3, Duration.ZERO);

    /** JetStream whose publish fails the first `failures` times; other methods are unsupported. */
    static JetStream flakyJs(int failures, AtomicInteger calls, List<String> msgIds) {
        return (JetStream) Proxy.newProxyInstance(
                JetStream.class.getClassLoader(), new Class<?>[] {JetStream.class}, (proxy, method, args) -> {
                    if (!method.getName().equals("publish") || args.length != 3) {
                        throw new UnsupportedOperationException(method.toString());
                    }
                    msgIds.add(((Headers) args[1]).getFirst("Nats-Msg-Id"));
                    if (calls.incrementAndGet() <= failures) {
                        throw new IOException("nats down");
                    }
                    return null;
                });
    }

    @Test
    void publisherRetriesThenThrows() {
        var calls = new AtomicInteger();
        var ids = new ArrayList<String>();
        var publisher = new EventPublisher(flakyJs(2, calls, ids), LibraryTest.CONTRACTS, "tlr-agent", NO_WAIT);
        Event event = publisher.publish("sandman.tracelinks.updated", "mini", DATA, null, "m-1", null);
        assertEquals(3, calls.get());
        assertEquals("sandman/tlr-agent", event.source());
        assertEquals(List.of(event.id(), event.id(), event.id()), ids);

        var failing = new AtomicInteger();
        var failingPublisher =
                new EventPublisher(flakyJs(10, failing, new ArrayList<>()), LibraryTest.CONTRACTS, "x", NO_WAIT);
        assertThrows(
                EventPublisher.PublishException.class,
                () -> failingPublisher.publish("sandman.tracelinks.updated", "mini", DATA, null, "m-1", null));
        assertEquals(4, failing.get());
        // completion event without causationid violates the envelope contract
        assertThrows(
                Contracts.ContractException.class,
                () -> publisher.publish("sandman.tracelinks.updated", "mini", DATA, null, null, null));
    }

    @Test
    void envelopeOmitsNullMembers() {
        Event event =
                Event.build(LibraryTest.CONTRACTS, "x", "sandman.tracelinks.updated", "mini", DATA, null, "m-1", "e-1");
        var json = event.toJson();
        assertTrue(!json.has("correlationid") && !json.has("traceparent"));
        assertEquals(event, Event.fromJson(json));
        assertNull(Event.fromJson(json).correlationId());
    }

    /** Non-JetStream message recording ack/nak (delivery count therefore 1). */
    static Message fakeMessage(byte[] data, List<String> calls) {
        return (Message) Proxy.newProxyInstance(
                Message.class.getClassLoader(),
                new Class<?>[] {Message.class},
                (proxy, method, args) -> switch (method.getName()) {
                    case "getData" -> data;
                    case "isJetStream" -> false;
                    case "ack" -> calls.add("ack");
                    case "nakWithDelay" -> calls.add("nak " + args[0]);
                    default -> throw new UnsupportedOperationException(method.toString());
                });
    }

    @Test
    void consumerAcksOnSuccessAndNaksWithBackoff() {
        Event event =
                Event.build(LibraryTest.CONTRACTS, "x", "sandman.tracelinks.updated", "mini", DATA, null, "m-1", null);
        byte[] payload = JsonMapper.shared().writeValueAsBytes(event.toJson());
        var seen = new ArrayList<Event>();
        var good = new ArrayList<String>();
        EventConsumer.handle(fakeMessage(payload, good), seen::add);
        assertEquals(List.of("ack"), good);
        assertEquals(event, seen.get(0));
        var bad = new ArrayList<String>();
        EventConsumer.handle(fakeMessage(payload, bad), e -> {
            throw new IllegalStateException("handler failed");
        });
        assertEquals(List.of("nak " + EventConsumer.BACKOFF.get(0)), bad);
    }
}
