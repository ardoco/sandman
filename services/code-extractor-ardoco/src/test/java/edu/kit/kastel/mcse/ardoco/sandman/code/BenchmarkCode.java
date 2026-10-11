package edu.kit.kastel.mcse.ardoco.sandman.code;

import java.io.IOException;
import java.io.InputStream;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.time.Duration;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.Map;
import org.junit.jupiter.api.Assumptions;

/**
 * Benchmark code of benchmark-code.txt: downloaded once, verified against the pinned sha256 and cached outside the
 * repository. Offline without a cache the calling test is skipped with the reason.
 */
final class BenchmarkCode {

    record Entry(String kind, String name, String url, String sha256) {}

    private static final Path CACHE = Path.of(System.getProperty("user.home"), ".cache", "sandman", "benchmark-code");

    private BenchmarkCode() {}

    static Map<String, Entry> entries() throws IOException {
        Map<String, Entry> entries = new LinkedHashMap<>();
        try (InputStream in = BenchmarkCode.class.getResourceAsStream("/benchmark-code.txt")) {
            for (String line : new String(in.readAllBytes()).split("\n")) {
                if (line.isBlank() || line.startsWith("#")) {
                    continue;
                }
                String[] f = line.trim().split("\\s+");
                entries.put(f[0] + ":" + f[1], new Entry(f[0], f[1], f[2], f[3]));
            }
        }
        return entries;
    }

    /** The cached file of the entry; skips the test if it is neither cached nor downloadable. */
    static Path file(String kind, String name) throws IOException {
        Entry entry = entries().get(kind + ":" + name);
        Path file =
                CACHE.resolve(name + "-" + entry.sha256().substring(0, 12) + (kind.equals("acm") ? ".acm" : ".tar.gz"));
        if (!Files.isRegularFile(file) || !sha256(file).equals(entry.sha256())) {
            Files.createDirectories(CACHE);
            Path partial = Files.createTempFile(CACHE, name, ".part");
            try {
                HttpResponse<Path> response = HttpClient.newBuilder()
                        .followRedirects(HttpClient.Redirect.NORMAL)
                        .connectTimeout(Duration.ofSeconds(15))
                        .build()
                        .send(
                                HttpRequest.newBuilder(URI.create(entry.url()))
                                        .timeout(Duration.ofMinutes(5))
                                        .build(),
                                HttpResponse.BodyHandlers.ofFile(partial));
                if (response.statusCode() != 200) {
                    throw new IOException("HTTP " + response.statusCode() + " for " + entry.url());
                }
            } catch (IOException e) {
                Files.deleteIfExists(partial);
                Assumptions.abort(
                        "benchmark code " + name + " is not cached and cannot be downloaded (offline?): " + e);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                Files.deleteIfExists(partial);
                throw new IOException(e);
            }
            String actual = sha256(partial);
            if (!actual.equals(entry.sha256())) {
                Files.deleteIfExists(partial);
                throw new AssertionError("sha256 of " + entry.url() + " is " + actual + ", pinned " + entry.sha256()
                        + " in benchmark-code.txt");
            }
            Files.move(partial, file, java.nio.file.StandardCopyOption.REPLACE_EXISTING);
        }
        return file;
    }

    /** Unpacks a code archive into dir (the single top-level directory is stripped) and returns dir. */
    static Path unpack(String name, Path dir) throws IOException, InterruptedException {
        Path archive = file("code", name);
        Files.createDirectories(dir);
        Process tar = new ProcessBuilder(
                        "tar", "-xzf", archive.toString(), "-C", dir.toString(), "--strip-components=1")
                .inheritIO()
                .start();
        if (tar.waitFor() != 0) {
            throw new IOException("tar failed for " + archive);
        }
        return dir;
    }

    private static String sha256(Path file) throws IOException {
        try (InputStream in = Files.newInputStream(file)) {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            byte[] buffer = new byte[1 << 16];
            for (int n; (n = in.read(buffer)) > 0; ) {
                digest.update(buffer, 0, n);
            }
            return HexFormat.of().formatHex(digest.digest());
        } catch (java.security.NoSuchAlgorithmException e) {
            throw new IllegalStateException(e);
        }
    }
}
