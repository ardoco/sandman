package edu.kit.kastel.mcse.ardoco.sandman.tlr.ardoco;

import java.io.IOException;
import java.io.InputStream;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.Map;
import java.util.concurrent.CompletableFuture;
import java.util.function.Consumer;
import org.a2aproject.sdk.client.http.A2AHttpClient;
import org.a2aproject.sdk.client.http.A2AHttpHeaders;
import org.a2aproject.sdk.client.http.A2AHttpResponse;
import org.a2aproject.sdk.client.http.ServerSentEvent;

/**
 * The HTTP client of the A2A calls to the code model plugin: connect and response-header timeouts, no redirects, and
 * a cap on the response body that is checked while the stream is read, so an oversized ACM is neither buffered nor
 * parsed (T2.1b criterion 5). Streaming (SSE) is not used by SANDMAN's A2A client and is not supported.
 */
final class CappedHttpClient implements A2AHttpClient, AutoCloseable {

    /** The body is larger than the cap; not retryable. */
    static final class TooLargeException extends RuntimeException {
        TooLargeException(long maxBytes) {
            super("response body is larger than " + maxBytes + " bytes");
        }
    }

    private final HttpClient http;
    private final Duration timeout;
    private final long maxBytes;

    CappedHttpClient(Duration timeout, long maxBytes) {
        this.http = HttpClient.newBuilder()
                .connectTimeout(timeout)
                .followRedirects(HttpClient.Redirect.NEVER)
                .build();
        this.timeout = timeout;
        this.maxBytes = maxBytes;
    }

    @Override
    public GetBuilder createGet() {
        return new Get();
    }

    @Override
    public PostBuilder createPost() {
        return new Post();
    }

    @Override
    public DeleteBuilder createDelete() {
        return new Delete();
    }

    @Override
    public void close() {
        http.shutdownNow();
    }

    private A2AHttpResponse send(HttpRequest.Builder request) throws IOException, InterruptedException {
        HttpResponse<InputStream> r =
                http.send(request.timeout(timeout).build(), HttpResponse.BodyHandlers.ofInputStream());
        try (InputStream in = r.body()) {
            byte[] body = in.readNBytes((int) Math.min(maxBytes + 1, Integer.MAX_VALUE));
            if (body.length > maxBytes) {
                throw new TooLargeException(maxBytes);
            }
            String text = new String(body, StandardCharsets.UTF_8);
            A2AHttpHeaders headers = A2AHttpHeaders.of(r.headers().map());
            return new A2AHttpResponse() {
                @Override
                public int status() {
                    return r.statusCode();
                }

                @Override
                public boolean success() {
                    return r.statusCode() >= 200 && r.statusCode() < 300;
                }

                @Override
                public String body() {
                    return text;
                }

                @Override
                public A2AHttpHeaders headers() {
                    return headers;
                }
            };
        }
    }

    private abstract class Req<T extends Builder<T>> implements Builder<T> {
        final HttpRequest.Builder request = HttpRequest.newBuilder();

        abstract T self();

        @Override
        public T url(String url) {
            request.uri(URI.create(url));
            return self();
        }

        @Override
        public T addHeaders(Map<String, String> headers) {
            headers.forEach(this::addHeader);
            return self();
        }

        @Override
        public T addHeader(String name, String value) {
            request.header(name, value);
            return self();
        }
    }

    private final class Get extends Req<GetBuilder> implements GetBuilder {
        @Override
        GetBuilder self() {
            return this;
        }

        @Override
        public A2AHttpResponse get() throws IOException, InterruptedException {
            return send(request.GET());
        }

        @Override
        public CompletableFuture<Void> getAsyncSSE(
                Consumer<ServerSentEvent> messageConsumer,
                Consumer<Throwable> errorConsumer,
                Runnable completeRunnable) {
            throw new UnsupportedOperationException("streaming is not used");
        }
    }

    private final class Post extends Req<PostBuilder> implements PostBuilder {
        private String body = "";

        @Override
        PostBuilder self() {
            return this;
        }

        @Override
        public PostBuilder body(String body) {
            this.body = body;
            return this;
        }

        @Override
        public A2AHttpResponse post() throws IOException, InterruptedException {
            return send(request.POST(HttpRequest.BodyPublishers.ofString(body)));
        }

        @Override
        public CompletableFuture<Void> postAsyncSSE(
                Consumer<ServerSentEvent> messageConsumer,
                Consumer<Throwable> errorConsumer,
                Runnable completeRunnable) {
            throw new UnsupportedOperationException("streaming is not used");
        }
    }

    private final class Delete extends Req<DeleteBuilder> implements DeleteBuilder {
        @Override
        DeleteBuilder self() {
            return this;
        }

        @Override
        public A2AHttpResponse delete() throws IOException, InterruptedException {
            return send(request.DELETE());
        }
    }
}
