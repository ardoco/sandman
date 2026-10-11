package edu.kit.kastel.mcse.ardoco.sandman.tlr.ardoco;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import com.sun.net.httpserver.HttpServer;
import java.io.IOException;
import java.net.InetSocketAddress;
import java.time.Duration;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

class CappedHttpClientTest {

    private HttpServer server;
    private String url;

    @BeforeEach
    void start() throws IOException {
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/small", x -> reply(x, 10));
        server.createContext("/big", x -> reply(x, 1000));
        server.createContext("/slow", x -> {
            try {
                Thread.sleep(3000);
            } catch (InterruptedException ignored) {
                // the client gave up
            }
            x.close();
        });
        server.start();
        url = "http://127.0.0.1:" + server.getAddress().getPort();
    }

    @AfterEach
    void stop() {
        server.stop(0);
    }

    private static void reply(com.sun.net.httpserver.HttpExchange x, int bytes) throws IOException {
        byte[] body = "a".repeat(bytes).getBytes();
        x.sendResponseHeaders(200, body.length);
        try (var out = x.getResponseBody()) {
            out.write(body);
        } catch (IOException ignored) {
            // the client stopped reading at the cap
        }
    }

    @Test
    void aBodyUpToTheCapIsReturnedAndALargerOneIsRejected() throws Exception {
        try (var client = new CappedHttpClient(Duration.ofSeconds(5), 100)) {
            assertEquals(10, client.createGet().url(url + "/small").get().body().length());
            assertThrows(
                    CappedHttpClient.TooLargeException.class,
                    () -> client.createPost().url(url + "/big").body("{}").post());
        }
    }

    @Test
    void anAnswerThatDoesNotStartIsATimeout() {
        try (var client = new CappedHttpClient(Duration.ofMillis(300), 100)) {
            assertThrows(
                    java.net.http.HttpTimeoutException.class,
                    () -> client.createGet().url(url + "/slow").get());
        }
    }
}
