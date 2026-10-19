package edu.kit.kastel.mcse.ardoco.sandman.ardocotext;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import java.io.IOException;
import java.net.InetSocketAddress;
import java.net.URI;
import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.function.Function;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

class CorenlpClientTest {

    private record Seen(String method, String path, String authorization, String body) {}

    private record Reply(int status, String body) {}

    private HttpServer server;
    private final List<Seen> seen = Collections.synchronizedList(new ArrayList<>());
    private volatile Function<Seen, Reply> handler;
    private final List<String> upgrades = Collections.synchronizedList(new ArrayList<>());

    @BeforeEach
    void start() throws IOException {
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/", this::handle);
        server.start();
    }

    @AfterEach
    void stop() {
        if (server != null) {
            server.stop(0);
        }
    }

    private void handle(HttpExchange ex) throws IOException {
        var s = new Seen(
                ex.getRequestMethod(),
                ex.getRequestURI().getPath(),
                ex.getRequestHeaders().getFirst("Authorization"),
                new String(ex.getRequestBody().readAllBytes(), StandardCharsets.UTF_8));
        seen.add(s);
        upgrades.add(String.valueOf(ex.getRequestHeaders().getFirst("Upgrade")));
        Reply r = handler.apply(s);
        byte[] bytes = r.body().getBytes(StandardCharsets.UTF_8);
        ex.sendResponseHeaders(r.status(), bytes.length);
        ex.getResponseBody().write(bytes);
        ex.close();
    }

    private CorenlpClient client(CorenlpClient.Eolonly mode, int retries) {
        return new CorenlpClient(
                URI.create("http://127.0.0.1:" + server.getAddress().getPort()),
                Duration.ofSeconds(5),
                mode,
                Collections.nCopies(retries, Duration.ZERO),
                "Basic Y2FsbGVyOnRva2Vu");
    }

    /** One word per sentence, which is the line itself; lines must not contain blanks. */
    private static String answer(String... sentences) {
        var out = new StringBuilder("{\"sentences\":[");
        for (int i = 0; i < sentences.length; i++) {
            out.append(i > 0 ? "," : "")
                    .append("{\"constituencyTree\":\"(ROOT (NN ")
                    .append(sentences[i])
                    .append("))\",\"sentenceNo\":")
                    .append(i + 1)
                    .append(",\"text\":\"")
                    .append(sentences[i])
                    .append("\",\"words\":[{\"id\":1,\"incomingDependencies\":[],\"lemma\":\"")
                    .append(sentences[i].toLowerCase())
                    .append("\",\"outgoingDependencies\":[],\"sentenceNo\":")
                    .append(i + 1)
                    .append(",\"text\":\"")
                    .append(sentences[i])
                    .append("\",\"posTag\":\"NN\"}]}");
        }
        return out.append("]}").toString();
    }

    private static String sentText(Seen s) {
        String encoded = s.body().replaceAll("(?s).*\"text\": \"([^\"]*)\".*", "$1");
        return URLDecoder.decode(encoded, StandardCharsets.UTF_8);
    }

    private static final List<String> LINES = List.of("Alpha", "Beta", "Gamma");

    @Test
    void requestsDoNotOfferAnH2cUpgrade() {
        handler = s -> s.path().endsWith("/version") ? new Reply(404, "{}") : new Reply(200, answer("Alpha"));
        var client = client(CorenlpClient.Eolonly.AUTO, 0);
        client.annotate(List.of("Alpha"), client.provider(() -> false), () -> false);
        assertFalse(upgrades.isEmpty());
        assertTrue(upgrades.stream().allMatch("null"::equals));
    }

    @Test
    void serviceWithoutVersionEndpointGetsTheInterimBlankLineSplit() {
        handler = s ->
                s.path().endsWith("/version") ? new Reply(404, "{}") : new Reply(200, answer("Alpha", "Beta", "Gamma"));
        var client = client(CorenlpClient.Eolonly.AUTO, 0);
        var provider = client.provider(() -> false);
        assertFalse(provider.eolonly());
        assertEquals("provider=unknown;eolonly=false", provider.identity());
        var text = client.annotate(LINES, provider, () -> false);
        assertEquals(3, text.getSentences().size());
        Seen post = seen.get(1);
        assertEquals("POST", post.method());
        assertEquals("/stanfordnlp", post.path());
        assertEquals("Alpha\n\nBeta\n\nGamma\n", sentText(post));
        assertFalse(post.body().contains("eolonly"));
    }

    @Test
    void serviceWithVersionEndpointGetsEolonlyAndItsVersionIsTheIdentity() {
        handler = s -> s.path().endsWith("/version")
                ? new Reply(200, "{\"service\": \"2.1.0\", \"corenlp\": \"4.5.10\"}")
                : new Reply(200, answer("Alpha", "Beta", "Gamma"));
        var client = client(CorenlpClient.Eolonly.AUTO, 0);
        var provider = client.provider(() -> false);
        assertTrue(provider.eolonly());
        assertEquals("provider={\"corenlp\":\"4.5.10\",\"service\":\"2.1.0\"};eolonly=true", provider.identity());
        client.annotate(LINES, provider, () -> false);
        Seen post = seen.get(1);
        assertEquals("Alpha\nBeta\nGamma\n", sentText(post));
        assertTrue(post.body().contains("\"eolonly\": true"), post.body());
    }

    @Test
    void settingForcesEolonlyOnOrOff() {
        handler = s -> new Reply(404, "");
        assertTrue(client(CorenlpClient.Eolonly.TRUE, 0).provider(() -> false).eolonly());
        handler = s -> new Reply(200, "{\"service\": \"x\"}");
        assertFalse(client(CorenlpClient.Eolonly.FALSE, 0).provider(() -> false).eolonly());
    }

    @Test
    void callerTokenIsSentOnEveryRequest() {
        handler = s -> s.path().endsWith("/version") ? new Reply(404, "") : new Reply(200, answer("Alpha"));
        var client = client(CorenlpClient.Eolonly.AUTO, 0);
        client.annotate(List.of("Alpha"), client.provider(() -> false), () -> false);
        assertEquals(
                List.of("Basic Y2FsbGVyOnRva2Vu", "Basic Y2FsbGVyOnRva2Vu"),
                seen.stream().map(Seen::authorization).toList());
    }

    @Test
    void moreSentencesThanLinesFailNamingTheFirstLine() {
        handler = s -> new Reply(200, answer("Alpha", "Beta", "Gamma", "Delta"));
        var e = assertThrows(IllegalStateException.class, () -> annotate());
        assertTrue(e.getMessage().contains("4 sentences for 3 lines"), e.getMessage());
    }

    @Test
    void fewerSentencesThanLinesFail() {
        handler = s -> new Reply(200, answer("Alpha", "Beta"));
        var e = assertThrows(IllegalStateException.class, () -> annotate());
        assertTrue(e.getMessage().contains("2 sentences for 3 lines"), e.getMessage());
        assertTrue(e.getMessage().contains("line 3"), e.getMessage());
    }

    @Test
    void changedTextFailsNamingTheFirstDifferingLine() {
        handler = s -> new Reply(200, answer("Alpha", "Betta", "Gamma"));
        var e = assertThrows(IllegalStateException.class, () -> annotate());
        assertTrue(e.getMessage().contains("at line 2 of 3"), e.getMessage());
        assertTrue(e.getMessage().contains("'Beta'") && e.getMessage().contains("'Betta'"), e.getMessage());
    }

    @Test
    void whitespaceDifferencesAreNormalizedAway() {
        handler = s -> new Reply(200, answer("Alpha", "Beta", "Gamma"));
        var client = client(CorenlpClient.Eolonly.TRUE, 0);
        client.annotate(List.of("Alpha ", " Beta", "Gamma"), client.provider(() -> false), () -> false);
    }

    private void annotate() {
        var client = client(CorenlpClient.Eolonly.TRUE, 0);
        client.annotate(LINES, new CorenlpClient.Provider(true, "x"), () -> false);
    }

    @Test
    void retryableStatusesAreRetriedUpToThreeAttempts() {
        for (int status : new int[] {429, 502, 503, 504}) {
            seen.clear();
            int[] calls = {0};
            handler = s -> ++calls[0] < 3 ? new Reply(status, "") : new Reply(200, answer("Alpha"));
            var client = client(CorenlpClient.Eolonly.TRUE, 2);
            var text = client.annotate(List.of("Alpha"), new CorenlpClient.Provider(true, "x"), () -> false);
            assertEquals(1, text.getSentences().size());
            assertEquals(3, seen.size(), "status " + status);
        }
    }

    @Test
    void exhaustedRetriesAreUnavailableAfterThreeAttempts() {
        handler = s -> new Reply(503, "");
        var client = client(CorenlpClient.Eolonly.TRUE, 2);
        assertThrows(
                CorenlpUnavailableException.class,
                () -> client.annotate(List.of("Alpha"), new CorenlpClient.Provider(true, "x"), () -> false));
        assertEquals(3, seen.size());
    }

    @Test
    void connectErrorsAreRetriedThenUnavailable() {
        int port = server.getAddress().getPort();
        server.stop(0);
        var client = new CorenlpClient(
                URI.create("http://127.0.0.1:" + port),
                Duration.ofSeconds(5),
                CorenlpClient.Eolonly.AUTO,
                List.of(Duration.ZERO, Duration.ZERO),
                null);
        var e = assertThrows(CorenlpUnavailableException.class, () -> client.provider(() -> false));
        assertTrue(e.getMessage().contains("unavailable"), e.getMessage());
        assertThrows(
                CorenlpUnavailableException.class,
                () -> client.annotate(List.of("Alpha"), new CorenlpClient.Provider(true, "x"), () -> false));
        server = null;
    }

    @Test
    void rejectedCredentialsFailAtOnceWithoutRetry() {
        for (int status : new int[] {401, 403}) {
            seen.clear();
            handler = s -> new Reply(status, "");
            var client = client(CorenlpClient.Eolonly.TRUE, 2);
            var e = assertThrows(
                    IllegalStateException.class,
                    () -> client.annotate(List.of("Alpha"), new CorenlpClient.Provider(true, "x"), () -> false));
            assertTrue(
                    e.getMessage().startsWith("CoreNLP credentials rejected: check deploy/secrets/corenlp_credentials"),
                    e.getMessage());
            assertEquals(1, seen.size(), "status " + status);
        }
    }

    @Test
    void otherStatusesFailAtOnceWithoutRetry() {
        handler = s -> new Reply(500, "boom");
        var client = client(CorenlpClient.Eolonly.TRUE, 2);
        var e = assertThrows(
                IllegalStateException.class,
                () -> client.annotate(List.of("Alpha"), new CorenlpClient.Provider(true, "x"), () -> false));
        assertTrue(e.getMessage().contains("500"), e.getMessage());
        assertEquals(1, seen.size());
    }

    @Test
    void slowAnswerIsUnavailableAfterTheTimeoutWithoutRetry() {
        handler = s -> {
            try {
                Thread.sleep(1500);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
            }
            return new Reply(200, answer("Alpha"));
        };
        var client = new CorenlpClient(
                URI.create("http://127.0.0.1:" + server.getAddress().getPort()),
                Duration.ofMillis(300),
                CorenlpClient.Eolonly.TRUE,
                List.of(Duration.ZERO, Duration.ZERO),
                null);
        var e = assertThrows(
                CorenlpUnavailableException.class,
                () -> client.annotate(List.of("Alpha"), new CorenlpClient.Provider(true, "x"), () -> false));
        assertTrue(e.getMessage().contains("did not answer"), e.getMessage());
        assertEquals(1, seen.size());
    }

    @Test
    void nonJsonAnswerFailsWithoutRetry() {
        handler = s -> new Reply(200, "<html>");
        assertThrows(Exception.class, () -> annotate());
    }
}
