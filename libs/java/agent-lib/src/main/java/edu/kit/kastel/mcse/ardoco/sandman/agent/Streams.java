package edu.kit.kastel.mcse.ardoco.sandman.agent;

import io.nats.client.JetStreamApiException;
import io.nats.client.JetStreamManagement;
import io.nats.client.api.DiscardPolicy;
import io.nats.client.api.RetentionPolicy;
import io.nats.client.api.StorageType;
import io.nats.client.api.StreamConfiguration;
import java.io.IOException;
import java.time.Duration;

/** The SANDMAN JetStream stream (Stage 2 §1.4). */
public final class Streams {

    public static final String STREAM = "SANDMAN";
    public static final StreamConfiguration CONFIG = StreamConfiguration.builder()
            .name(STREAM)
            .subjects("sandman.>")
            .storageType(StorageType.File)
            .retentionPolicy(RetentionPolicy.Limits)
            .maxAge(Duration.ofDays(90))
            .duplicateWindow(Duration.ofMinutes(2))
            .discardPolicy(DiscardPolicy.Old)
            .replicas(1)
            .build();
    private static final int STREAM_NOT_FOUND = 10059;

    private Streams() {}

    /** Creates or updates the stream (idempotent). */
    public static void ensure(JetStreamManagement jsm) throws IOException, JetStreamApiException {
        try {
            jsm.getStreamInfo(STREAM);
        } catch (JetStreamApiException e) {
            if (e.getApiErrorCode() != STREAM_NOT_FOUND) {
                throw e;
            }
            jsm.addStream(CONFIG);
            return;
        }
        jsm.updateStream(CONFIG);
    }
}
