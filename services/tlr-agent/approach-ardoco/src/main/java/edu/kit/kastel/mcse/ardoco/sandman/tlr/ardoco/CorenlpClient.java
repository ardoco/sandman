package edu.kit.kastel.mcse.ardoco.sandman.tlr.ardoco;

import edu.kit.kastel.mcse.ardoco.core.api.text.Text;
import edu.kit.kastel.mcse.ardoco.core.common.JsonHandling;
import edu.kit.kastel.mcse.ardoco.core.textproviderjson.converter.DtoToObjectConverter;
import edu.kit.kastel.mcse.ardoco.core.textproviderjson.dto.TextDto;
import edu.kit.kastel.mcse.ardoco.core.textproviderjson.error.NotConvertableException;
import edu.kit.kastel.mcse.ardoco.sandman.agent.Jcs;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.ApproachUnavailableException;
import java.io.IOException;
import java.io.UncheckedIOException;
import java.net.URI;
import java.net.URLEncoder;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.net.http.HttpTimeoutException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.util.Base64;
import java.util.List;
import java.util.Optional;
import java.util.Set;
import java.util.function.BooleanSupplier;
import tools.jackson.databind.json.JsonMapper;

/**
 * Client of the Stanford CoreNLP provider service behind the corenlp-gateway (T2.5). It holds no credentials: the
 * gateway adds them. ArDoCo's own provider selection (ConfigManager) is never used, so its silent local fallback cannot
 * run. Converts the answer with ArDoCo's DtoToObjectConverter, as ArDoCo's TextProcessorService does.
 */
class CorenlpClient {

    static final String DEFAULT_URL = "http://corenlp-gateway:8080";
    private static final String PATH = "/stanfordnlp";
    private static final Set<Integer> RETRY_STATUS = Set.of(429, 502, 503, 504);
    static final List<Duration> BACKOFF = List.of(Duration.ofSeconds(2), Duration.ofSeconds(8));
    static final String REJECTED =
            "CoreNLP credentials rejected: check deploy/secrets/corenlp_credentials (or the caller token corenlp_client_token_*)";

    /** Request field eolonly: AUTO asks GET /stanfordnlp/version (404 = service without the field). */
    enum Eolonly {
        AUTO,
        TRUE,
        FALSE
    }

    private final URI base;
    private final Duration timeout;
    private final Eolonly eolonly;
    private final List<Duration> backoff;
    private final String authorization;
    private final HttpClient http;

    /** authorization: the header value that authenticates this caller at the corenlp-gateway, null for none. */
    CorenlpClient(URI base, Duration timeout, Eolonly eolonly, List<Duration> backoff, String authorization) {
        this.authorization = authorization;
        this.base = base;
        this.timeout = timeout;
        this.eolonly = eolonly;
        this.backoff = backoff;
        this.http = HttpClient.newBuilder()
                .connectTimeout(Duration.ofSeconds(10))
                .followRedirects(HttpClient.Redirect.NEVER)
                .build();
    }

    /** Environment variable, else JVM system property of the same name (tests start their own provider). */
    private static String setting(String name, String fallback) {
        String v = System.getenv(name);
        if (v == null || v.isBlank()) {
            v = System.getProperty(name);
        }
        return v == null || v.isBlank() ? fallback : v;
    }

    /**
     * SANDMAN_CORENLP_URL, SANDMAN_CORENLP_TIMEOUT (seconds), SANDMAN_CORENLP_EOLONLY (auto, true, false), and the
     * gateway token of this caller: file SANDMAN_CORENLP_TOKEN_FILE, user SANDMAN_CORENLP_USER (default
     * SANDMAN_SERVICE_NAME, HTTP Basic).
     */
    static CorenlpClient fromEnv() {
        long seconds = Long.parseLong(setting("SANDMAN_CORENLP_TIMEOUT", "300"));
        Eolonly mode =
                Eolonly.valueOf(setting("SANDMAN_CORENLP_EOLONLY", "auto").toUpperCase(java.util.Locale.ROOT));
        String tokenFile = setting("SANDMAN_CORENLP_TOKEN_FILE", null);
        String authorization = null;
        if (tokenFile != null) {
            try {
                String token = Files.readString(Path.of(tokenFile)).strip();
                String user = setting("SANDMAN_CORENLP_USER", setting("SANDMAN_SERVICE_NAME", "tlr-agent"));
                authorization = "Basic "
                        + Base64.getEncoder().encodeToString((user + ":" + token).getBytes(StandardCharsets.UTF_8));
            } catch (IOException e) {
                throw new UncheckedIOException("cannot read the CoreNLP gateway token " + tokenFile, e);
            }
        }
        return new CorenlpClient(
                URI.create(setting("SANDMAN_CORENLP_URL", DEFAULT_URL)),
                Duration.ofSeconds(seconds),
                mode,
                BACKOFF,
                authorization);
    }

    String authorization() {
        return authorization;
    }

    /** What the next annotation will use: whether the request carries eolonly, and the /version answer (or unknown). */
    record Provider(boolean eolonly, String version) {
        String identity() {
            return "provider=" + version + ";eolonly=" + eolonly;
        }
    }

    Provider provider(BooleanSupplier cancelled) {
        Optional<String> version = version(cancelled);
        boolean eol =
                switch (eolonly) {
                    case TRUE -> true;
                    case FALSE -> false;
                    case AUTO -> version.isPresent();
                };
        return new Provider(eol, version.orElse("unknown"));
    }

    /** The canonical /version answer, empty if the service has no such endpoint (404). */
    private Optional<String> version(BooleanSupplier cancelled) {
        HttpResponse<String> r = send(request("/version").GET().build(), cancelled, true);
        if (r.statusCode() == 404) {
            return Optional.empty();
        }
        if (r.statusCode() != 200) {
            throw new IllegalStateException("CoreNLP service answered " + r.statusCode() + " for /version");
        }
        try {
            return Optional.of(
                    new String(Jcs.canonicalize(JsonMapper.shared().readTree(r.body())), StandardCharsets.UTF_8));
        } catch (RuntimeException e) {
            throw new IllegalStateException("CoreNLP /version answer is not JSON", e);
        }
    }

    /**
     * Annotates the lines and checks that sentence i is line i. With eolonly the lines are separated by one newline,
     * else (interim, service without U-1) by a blank line, where CoreNLP's default newlineIsSentenceBreak=two splits.
     */
    Text annotate(List<String> lines, Provider provider, BooleanSupplier cancelled) {
        String text = String.join(provider.eolonly() ? "\n" : "\n\n", lines) + "\n";
        TextDto dto = request(text, provider.eolonly(), cancelled);
        check(lines, dto);
        return convert(dto);
    }

    /** One POST /stanfordnlp: the service answer for the text as is (the equivalence test sends raw texts). */
    TextDto request(String text, boolean eolonly, BooleanSupplier cancelled) {
        String body = "{\"text\": \"" + URLEncoder.encode(text, StandardCharsets.UTF_8) + "\""
                + (eolonly ? ", \"eolonly\": true" : "") + "}";
        HttpResponse<String> r = send(
                request("")
                        .header("Content-Type", "application/json")
                        .POST(HttpRequest.BodyPublishers.ofString(body))
                        .build(),
                cancelled,
                false);
        if (r.statusCode() != 200) {
            throw new IllegalStateException("CoreNLP service answered " + r.statusCode());
        }
        try {
            // ArDoCo's JsonConverter.fromJsonString also validates against the text schema with json-schema-validator
            // 2.0.0; SANDMAN pins 3.0.8 (Jackson 3) for the contracts and both cannot share a class path. The 1:1 check
            // and the converter reject answers of another shape.
            return JsonHandling.createObjectMapper().readValue(r.body(), TextDto.class);
        } catch (IOException e) {
            throw new IllegalStateException("CoreNLP answer is not a text DTO: " + e.getMessage(), e);
        }
    }

    static Text convert(TextDto dto) {
        try {
            return new DtoToObjectConverter().convertText(dto);
        } catch (NotConvertableException e) {
            throw new IllegalStateException("CoreNLP answer cannot be converted: " + e.getMessage(), e);
        }
    }

    /** The answer has one sentence per line, and sentence i normalized equals line i; names the first difference. */
    static void check(List<String> lines, TextDto dto) {
        var sentences = dto.getSentences();
        for (int i = 0; i < Math.min(lines.size(), sentences.size()); i++) {
            String expected = SentenceRendering.normalize(lines.get(i));
            String actual = SentenceRendering.normalize(sentences.get(i).getText());
            if (!expected.equals(actual)) {
                throw new IllegalStateException("CoreNLP sentence split differs from the rendered lines at line "
                        + (i + 1)
                        + " of " + lines.size() + ": line '" + shorten(expected) + "', sentence '" + shorten(actual)
                        + "'");
            }
        }
        if (sentences.size() != lines.size()) {
            throw new IllegalStateException("CoreNLP returned " + sentences.size() + " sentences for " + lines.size()
                    + " lines; the first line without a sentence is line "
                    + (Math.min(lines.size(), sentences.size()) + 1));
        }
    }

    private static String shorten(String s) {
        return s.length() <= 80 ? s : s.substring(0, 77) + "...";
    }

    private HttpRequest.Builder request(String suffix) {
        HttpRequest.Builder b = HttpRequest.newBuilder(
                        URI.create(base.toString().replaceAll("/+$", "") + PATH + suffix))
                .timeout(timeout);
        return authorization == null ? b : b.header("Authorization", authorization);
    }

    /** Retries connect errors and 429, 502, 503, 504; 401 and 403 fail at once; exhausted retries are "unavailable". */
    private HttpResponse<String> send(HttpRequest request, BooleanSupplier cancelled, boolean quick) {
        Exception last = null;
        int attempts = backoff.size() + 1;
        for (int attempt = 0; attempt < attempts; attempt++) {
            if (cancelled.getAsBoolean()) {
                throw new java.util.concurrent.CancellationException("cancelled");
            }
            try {
                HttpResponse<String> r = http.send(request, HttpResponse.BodyHandlers.ofString());
                if (r.statusCode() == 401 || r.statusCode() == 403) {
                    throw new IllegalStateException(REJECTED);
                }
                if (!RETRY_STATUS.contains(r.statusCode())) {
                    return r;
                }
                last = new IOException("status " + r.statusCode());
            } catch (HttpTimeoutException e) {
                throw new ApproachUnavailableException(
                        "CoreNLP service did not answer within " + timeout.toSeconds() + " s", e);
            } catch (IOException e) {
                last = e;
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                throw new java.util.concurrent.CancellationException("interrupted");
            }
            if (quick || attempt == attempts - 1) {
                break;
            }
            try {
                Thread.sleep(backoff.get(attempt));
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                throw new java.util.concurrent.CancellationException("interrupted");
            }
        }
        throw new ApproachUnavailableException(
                "CoreNLP service unavailable at " + base + " (" + last.getMessage() + ")", last);
    }
}
