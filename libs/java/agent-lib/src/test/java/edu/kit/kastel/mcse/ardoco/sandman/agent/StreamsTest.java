package edu.kit.kastel.mcse.ardoco.sandman.agent;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertSame;
import static org.junit.jupiter.api.Assertions.assertThrows;

import io.nats.client.JetStreamApiException;
import io.nats.client.JetStreamManagement;
import io.nats.client.api.Error;
import java.lang.reflect.Proxy;
import java.util.ArrayList;
import java.util.List;
import org.junit.jupiter.api.Test;

class StreamsTest {

    /** JetStreamApiException with a chosen API error code (the NATS Error constructors are not public). */
    static class ApiError extends JetStreamApiException {
        private final int code;

        ApiError(int code) {
            super(Error.JsBadRequestErr);
            this.code = code;
        }

        @Override
        public int getApiErrorCode() {
            return code;
        }
    }

    /** JetStreamManagement whose getStreamInfo throws `error` (if not null); records the other calls. */
    static JetStreamManagement jsm(JetStreamApiException error, List<String> calls) {
        return (JetStreamManagement) Proxy.newProxyInstance(
                JetStreamManagement.class.getClassLoader(), new Class<?>[] {JetStreamManagement.class}, (p, m, a) -> {
                    calls.add(m.getName());
                    if (m.getName().equals("getStreamInfo") && error != null) {
                        throw error;
                    }
                    return null;
                });
    }

    @Test
    void createsMissingStreamAndUpdatesExistingOne() throws Exception {
        List<String> calls = new ArrayList<>();
        Streams.ensure(jsm(new ApiError(10059), calls));
        assertEquals(List.of("getStreamInfo", "addStream"), calls);
        calls.clear();
        Streams.ensure(jsm(null, calls));
        assertEquals(List.of("getStreamInfo", "updateStream"), calls);
    }

    @Test
    void otherApiErrorsPropagate() {
        var error = new ApiError(10000);
        List<String> calls = new ArrayList<>();
        assertSame(error, assertThrows(JetStreamApiException.class, () -> Streams.ensure(jsm(error, calls))));
        assertEquals(List.of("getStreamInfo"), calls);
    }
}
