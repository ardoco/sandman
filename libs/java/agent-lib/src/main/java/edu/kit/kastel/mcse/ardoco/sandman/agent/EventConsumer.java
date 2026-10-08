package edu.kit.kastel.mcse.ardoco.sandman.agent;

import io.nats.client.JetStream;
import io.nats.client.JetStreamApiException;
import io.nats.client.JetStreamSubscription;
import io.nats.client.Message;
import io.nats.client.PullSubscribeOptions;
import io.nats.client.api.AckPolicy;
import io.nats.client.api.ConsumerConfiguration;
import java.io.IOException;
import java.time.Duration;
import java.util.List;
import java.util.function.BooleanSupplier;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import tools.jackson.databind.json.JsonMapper;

/** Durable pull consumer (Stage 2 §1.4): ack on success, nak with the backoff delay on handler exceptions. */
public final class EventConsumer {

    private static final Logger LOG = LoggerFactory.getLogger(EventConsumer.class);
    public static final List<Duration> BACKOFF =
            List.of(Duration.ofSeconds(5), Duration.ofSeconds(30), Duration.ofSeconds(120), Duration.ofSeconds(300));

    /** Handles one event; an exception naks the message. */
    @FunctionalInterface
    public interface Handler {
        void handle(Event event) throws Exception;
    }

    private final JetStream js;
    private final String durable;
    private final String subject;

    public EventConsumer(JetStream js, String durable) {
        this(js, durable, "sandman.>");
    }

    public EventConsumer(JetStream js, String durable, String subject) {
        this.js = js;
        this.durable = durable;
        this.subject = subject;
    }

    public ConsumerConfiguration config() {
        return ConsumerConfiguration.builder()
                .durable(durable)
                .filterSubject(subject)
                .ackPolicy(AckPolicy.Explicit)
                .ackWait(Duration.ofSeconds(60))
                .maxDeliver(5)
                .backoff(BACKOFF.toArray(Duration[]::new))
                .maxAckPending(100)
                .build();
    }

    /** Fetches and handles messages until stop returns true; blocking, run it on a (virtual) thread. */
    public void run(Handler handler, BooleanSupplier stop) throws IOException, JetStreamApiException {
        JetStreamSubscription sub = js.subscribe(
                subject,
                PullSubscribeOptions.builder().stream(Streams.STREAM)
                        .configuration(config())
                        .build());
        try {
            while (!stop.getAsBoolean()) {
                for (Message msg : sub.fetch(10, Duration.ofSeconds(2))) {
                    handle(msg, handler);
                }
            }
        } finally {
            sub.unsubscribe();
        }
    }

    /** Acks after the handler succeeded; naks with the backoff delay for the delivery count otherwise. */
    static void handle(Message msg, Handler handler) {
        try {
            Event event = Event.fromJson(JsonMapper.shared().readTree(msg.getData()));
            Telemetry.consume(event, () -> {
                handler.handle(event);
                return null;
            });
        } catch (Exception e) {
            long delivered = msg.isJetStream() ? Math.max(1, msg.metaData().deliveredCount()) : 1;
            LOG.error("event handler failed (delivered {})", delivered, e);
            msg.nakWithDelay(BACKOFF.get((int) Math.min(delivered, BACKOFF.size()) - 1));
            return;
        }
        msg.ack();
    }
}
