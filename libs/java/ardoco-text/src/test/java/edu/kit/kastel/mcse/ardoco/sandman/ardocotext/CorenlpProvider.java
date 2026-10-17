package edu.kit.kastel.mcse.ardoco.sandman.ardocotext;

import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Base64;
import java.util.regex.Pattern;
import org.testcontainers.containers.GenericContainer;
import org.testcontainers.utility.DockerImageName;

/**
 * The CoreNLP provider service of the compose profile corenlp-local (same image digest), started once per JVM with
 * known credentials. The tests reach it directly: the client sends the credentials as its gateway token.
 */
public final class CorenlpProvider {

    private static final String USER = "sandman";
    private static final String PASSWORD = "test-password-0123456789abcdef0123456789";
    private static GenericContainer<?> container;

    private CorenlpProvider() {}

    /** The image reference of deploy/docker-compose.yml, so that tests and deployment cannot drift apart. */
    static String image() throws IOException {
        String override = System.getProperty("corenlp.image"); // optional: a local build of a newer service
        if (override != null) {
            return override;
        }
        for (Path dir : new Path[] {Path.of("../../../deploy"), Path.of("../../deploy")}) {
            Path compose = dir.resolve("docker-compose.yml");
            if (Files.exists(compose)) {
                var m = Pattern.compile("image: (ghcr\\.io/ardoco/stanfordcorenlp-provider-service:\\S+)")
                        .matcher(Files.readString(compose));
                if (m.find()) {
                    return m.group(1);
                }
            }
        }
        throw new IllegalStateException("provider image not found in deploy/docker-compose.yml");
    }

    /** Starts the container if needed and sets the system properties the adapter reads; returns the base URL. */
    public static synchronized String start() throws Exception {
        if (container == null) {
            // the compose reference carries tag and digest; Testcontainers takes the digest form
            String ref = image().replaceFirst(":[^:@]*@", "@");
            container = new GenericContainer<>(DockerImageName.parse(ref))
                    .withEnv("USERNAME", USER)
                    .withEnv("PASSWORD", PASSWORD)
                    .withEnv("JAVA_TOOL_OPTIONS", "-XX:MaxRAMPercentage=75")
                    .withCreateContainerCmdModifier(c -> c.getHostConfig().withMemory(4L << 30))
                    .withExposedPorts(8080);
            container.start();
            waitHealthy();
            Path token = Files.createTempFile("corenlp-token", ".txt");
            Files.writeString(token, PASSWORD);
            token.toFile().deleteOnExit();
            System.setProperty("SANDMAN_CORENLP_TOKEN_FILE", token.toString());
            System.setProperty("SANDMAN_CORENLP_USER", USER);
        }
        String url = "http://" + container.getHost() + ":" + container.getMappedPort(8080);
        System.setProperty("SANDMAN_CORENLP_URL", url);
        return url;
    }

    /** Peak resident set size of the provider process in MiB, from the container's cgroup. */
    public static long peakMemoryMiB() throws Exception {
        var r = container.execInContainer(
                "sh",
                "-c",
                "cat /sys/fs/cgroup/memory.peak 2>/dev/null || cat /sys/fs/cgroup/memory/memory.max_usage_in_bytes");
        return Long.parseLong(r.getStdout().trim()) >> 20;
    }

    private static void waitHealthy() throws Exception {
        String basic = Base64.getEncoder().encodeToString((USER + ":" + PASSWORD).getBytes(StandardCharsets.UTF_8));
        var http = HttpClient.newHttpClient();
        var request = HttpRequest.newBuilder(URI.create(
                        "http://" + container.getHost() + ":" + container.getMappedPort(8080) + "/stanfordnlp/health"))
                .header("Authorization", "Basic " + basic)
                .build();
        for (int i = 0; i < 120; i++) {
            try {
                if (http.send(request, HttpResponse.BodyHandlers.discarding()).statusCode() == 200) {
                    return;
                }
            } catch (IOException e) {
                // not up yet
            }
            Thread.sleep(1000);
        }
        throw new IllegalStateException("CoreNLP provider container did not become healthy");
    }
}
