package edu.kit.kastel.mcse.ardoco.sandman.tlr.ardoco;

import edu.kit.kastel.mcse.ardoco.sandman.agent.a2a.A2aClient;
import edu.kit.kastel.mcse.ardoco.sandman.ardocotext.CorenlpClient;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.ApproachUnavailableException;
import io.opentelemetry.context.Context;
import java.io.IOException;
import java.io.UncheckedIOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.CancellationException;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;
import java.util.function.BooleanSupplier;
import java.util.function.Supplier;
import org.a2aproject.sdk.A2A;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

/**
 * Client of the plugin skill {@code sandman.plugin.code-model} (T2.1b): submits the current code, polls the task and
 * cancels it when the run is cancelled or the timeout passes. The token is the tlr-agent's own inbound token of the
 * plugin; it is read from a file and never logged.
 */
class CodeModelClient implements AutoCloseable {

    static final String SKILL = "sandman.plugin.code-model";
    static final String DEFAULT_URL = "http://code-extractor-ardoco:8000";
    static final Duration DEFAULT_TIMEOUT = Duration.ofMinutes(10);
    /** Largest serialized ACM (T2.1b criterion 5). */
    static final long MAX_ACM_BYTES = 32L << 20;
    /** Cancel is best effort and must not delay the failure. */
    private static final Duration CANCEL_LIMIT = Duration.ofSeconds(2);

    private static final ExecutorService CALLS = Executors.newVirtualThreadPerTaskExecutor();

    /** A file of the current code. */
    record File(String path, String storagePath) {}

    /** The A2A calls; the tests replace them. */
    interface Calls {
        String identity(String url);

        A2aClient.TaskSnapshot submit(String url, JsonNode input, String messageId);

        A2aClient.TaskSnapshot get(String url, String taskId);

        A2aClient.TaskSnapshot cancel(String url, String taskId);

        default void close() {}
    }

    private final String url;
    private final Duration timeout;
    private final Duration poll;
    private final Calls calls;

    CodeModelClient(String url, Duration timeout, Duration poll, Calls calls) {
        this.url = url;
        this.timeout = timeout;
        this.poll = poll;
        this.calls = calls;
    }

    /**
     * SANDMAN_CODE_MODEL_URL, SANDMAN_CODE_MODEL_TIMEOUT (seconds, or a number with s or m) and the token file
     * SANDMAN_CODE_MODEL_TOKEN_FILE (read at the first call).
     */
    static CodeModelClient fromEnv() {
        String setting = CorenlpClient.setting("SANDMAN_CODE_MODEL_TIMEOUT", null);
        Duration t = setting == null ? DEFAULT_TIMEOUT : duration(setting);
        return new CodeModelClient(
                CorenlpClient.setting("SANDMAN_CODE_MODEL_URL", DEFAULT_URL),
                t,
                Duration.ofMillis(500),
                new A2aCalls(CorenlpClient.setting("SANDMAN_CODE_MODEL_TOKEN_FILE", null), t, MAX_ACM_BYTES));
    }

    static Duration duration(String text) {
        String t = text.strip();
        if (t.endsWith("m")) {
            return Duration.ofMinutes(Long.parseLong(t.substring(0, t.length() - 1)));
        }
        return Duration.ofSeconds(Long.parseLong(t.endsWith("s") ? t.substring(0, t.length() - 1) : t));
    }

    /** Name and version of the plugin from its Agent Card; enters configHash. */
    String identity() {
        return guarded(timeout, () -> calls.identity(url));
    }

    /**
     * The ACM of the files. Plugin down, {@code DEPENDENCY_UNAVAILABLE}, retryable {@code INTERNAL} and a timeout are
     * ApproachUnavailableException; any other plugin error is an IllegalStateException (not retryable).
     */
    JsonNode acm(String projectId, List<File> files, BooleanSupplier cancelled) {
        ObjectNode input = JsonMapper.shared().createObjectNode();
        input.put("projectId", projectId);
        var current = input.putArray("currentArtifacts");
        files.forEach(f -> current.addObject().put("path", f.path()).put("storagePath", f.storagePath()));
        long deadline = System.nanoTime() + timeout.toNanos();
        A2aClient.TaskSnapshot task = guarded(
                timeout, () -> calls.submit(url, input, UUID.randomUUID().toString()));
        while (!task.terminal()) {
            if (cancelled.getAsBoolean()) {
                cancelQuietly(task);
                throw new CancellationException("cancelled");
            }
            if (System.nanoTime() > deadline) {
                cancelQuietly(task);
                throw new ApproachUnavailableException(
                        "code model plugin did not answer within " + timeout.toSeconds() + " s", null);
            }
            try {
                Thread.sleep(poll.toMillis());
            } catch (InterruptedException e) {
                cancelQuietly(task);
                Thread.currentThread().interrupt();
                throw new CancellationException("interrupted");
            }
            String id = task.taskId();
            Duration left = Duration.ofNanos(Math.max(deadline - System.nanoTime(), TimeUnit.MILLISECONDS.toNanos(1)));
            try {
                task = guarded(left, () -> calls.get(url, id));
            } catch (ApproachUnavailableException e) {
                cancelQuietly(task);
                throw e;
            }
        }
        return result(task);
    }

    private static JsonNode result(A2aClient.TaskSnapshot task) {
        if ("TASK_STATE_COMPLETED".equals(task.state())) {
            if (task.result() == null || !task.result().has("acm")) {
                throw new IllegalStateException("code model plugin returned no acm");
            }
            return task.result().get("acm");
        }
        String code =
                task.error() == null ? task.state() : task.error().path("code").asString(task.state());
        String message =
                task.error() == null ? "" : task.error().path("message").asString("");
        boolean retryable =
                task.error() != null && task.error().path("retryable").asBoolean(false);
        if ("DEPENDENCY_UNAVAILABLE".equals(code)
                || ("INTERNAL".equals(code) && retryable)
                || "TASK_STATE_CANCELED".equals(task.state())) {
            throw new ApproachUnavailableException("code model plugin: " + code + ": " + message, null);
        }
        throw new IllegalStateException("code model plugin failed: " + code + ": " + message);
    }

    /**
     * Runs the call on its own thread and waits at most {@code limit}: a hung call (connect, answer, body) ends as
     * ApproachUnavailableException (retryable). A response above the ACM cap is an IllegalStateException (not
     * retryable); a cancellation passes.
     */
    private <T> T guarded(Duration limit, Supplier<T> call) {
        Future<T> future = CALLS.submit(Context.current().wrap(call::get));
        try {
            return future.get(Math.max(limit.toNanos(), 1), TimeUnit.NANOSECONDS);
        } catch (TimeoutException e) {
            future.cancel(true);
            throw new ApproachUnavailableException(
                    "code model plugin did not answer within " + Math.max(limit.toSeconds(), 1) + " s", e);
        } catch (InterruptedException e) {
            future.cancel(true);
            Thread.currentThread().interrupt();
            throw new CancellationException("interrupted");
        } catch (ExecutionException e) {
            for (Throwable t = e.getCause(); t != null; t = t.getCause()) {
                if (t instanceof CappedHttpClient.TooLargeException) {
                    throw new IllegalStateException(
                            "code model plugin: the ACM is larger than " + (MAX_ACM_BYTES >> 20) + " MiB", t);
                }
                if (t instanceof CancellationException c) {
                    throw c;
                }
            }
            throw new ApproachUnavailableException(
                    "code model plugin unavailable at " + url + " (" + e.getCause() + ")", e.getCause());
        }
    }

    private void cancelQuietly(A2aClient.TaskSnapshot task) {
        try {
            guarded(
                    timeout.compareTo(CANCEL_LIMIT) < 0 ? timeout : CANCEL_LIMIT,
                    () -> calls.cancel(url, task.taskId()));
        } catch (RuntimeException e) {
            // the plugin drops the task when it is done; nothing more to do
        }
    }

    @Override
    public void close() {
        calls.close();
    }

    /** Writes the ACM as the plugin returned it and returns the sha256 of the bytes. */
    static String write(JsonNode acm, Path file) {
        try {
            byte[] bytes = JsonMapper.shared().writeValueAsBytes(acm);
            if (bytes.length > MAX_ACM_BYTES) {
                throw new IllegalStateException("code model plugin: the ACM is " + bytes.length + " bytes, larger than "
                        + (MAX_ACM_BYTES >> 20) + " MiB");
            }
            Files.write(file, bytes);
            return edu.kit.kastel.mcse.ardoco.sandman.agent.Ids.sha256Hex(bytes);
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }
    }

    /** A2A over {@link A2aClient} with the token of the file; the file is read when the first call needs it. */
    private static final class A2aCalls implements Calls {
        private final String tokenFile;
        private final CappedHttpClient http;
        private A2aClient client;

        A2aCalls(String tokenFile, Duration timeout, long maxBytes) {
            this.tokenFile = tokenFile;
            this.http = new CappedHttpClient(timeout, maxBytes);
        }

        @Override
        public String identity(String url) {
            var card = A2A.getAgentCard(http, url);
            return card.name() + "@" + card.version();
        }

        private synchronized A2aClient client() {
            if (client == null) {
                if (tokenFile == null) {
                    throw new IllegalStateException("SANDMAN_CODE_MODEL_TOKEN_FILE is not set");
                }
                try {
                    client = new A2aClient(
                            Files.readString(Path.of(tokenFile), StandardCharsets.UTF_8)
                                    .strip(),
                            http);
                } catch (IOException e) {
                    throw new UncheckedIOException("cannot read the code model token " + tokenFile, e);
                }
            }
            return client;
        }

        @Override
        public A2aClient.TaskSnapshot submit(String url, JsonNode input, String messageId) {
            return client().submit(url, SKILL, input, null, null, messageId);
        }

        @Override
        public A2aClient.TaskSnapshot get(String url, String taskId) {
            return client().get(url, taskId);
        }

        @Override
        public A2aClient.TaskSnapshot cancel(String url, String taskId) {
            return client().cancel(url, taskId);
        }

        @Override
        public synchronized void close() {
            if (client != null) {
                client.close();
            }
            http.close();
        }
    }
}
