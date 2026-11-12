package edu.kit.kastel.mcse.ardoco.sandman.inconsistency.ardoco;

import edu.kit.kastel.mcse.ardoco.sandman.agent.a2a.A2aClient;
import edu.kit.kastel.mcse.ardoco.sandman.ardocotext.CorenlpClient;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.util.List;
import java.util.UUID;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

/**
 * Production {@link ArtemisIdDetector.CodeModelSource}: asks the plugin skill {@code sandman.plugin.code-model} for the
 * ACM. The token is the inconsistency-agent's own inbound token of the plugin; it is read from a file at the first call
 * and never logged. Plugin errors and a timeout are an {@link IOException}, so the run fails instead of reporting
 * findings without code.
 */
final class PluginCodeModelSource implements ArtemisIdDetector.CodeModelSource {

    static final String SKILL = "sandman.plugin.code-model";
    static final String DEFAULT_URL = "http://code-extractor-ardoco:8000";
    static final String FILE_NAME = "codeModel.acm";

    interface Calls {
        A2aClient.TaskSnapshot submit(String url, JsonNode input, String messageId);

        A2aClient.TaskSnapshot get(String url, String taskId);
    }

    private final String url;
    private final Duration timeout;
    private final Duration poll;
    private final Calls calls;

    PluginCodeModelSource(String url, Duration timeout, Duration poll, Calls calls) {
        this.url = url;
        this.timeout = timeout;
        this.poll = poll;
        this.calls = calls;
    }

    /** SANDMAN_CODE_MODEL_URL, SANDMAN_CODE_MODEL_TIMEOUT (seconds) and SANDMAN_CODE_MODEL_TOKEN_FILE. */
    static PluginCodeModelSource fromEnv() {
        String tokenFile = CorenlpClient.setting("SANDMAN_CODE_MODEL_TOKEN_FILE", null);
        long seconds = Long.parseLong(CorenlpClient.setting("SANDMAN_CODE_MODEL_TIMEOUT", "600"));
        return new PluginCodeModelSource(
                CorenlpClient.setting("SANDMAN_CODE_MODEL_URL", DEFAULT_URL),
                Duration.ofSeconds(seconds),
                Duration.ofMillis(500),
                new Calls() {
                    private A2aClient client;

                    private synchronized A2aClient client() {
                        if (client == null) {
                            if (tokenFile == null) {
                                throw new IllegalStateException("SANDMAN_CODE_MODEL_TOKEN_FILE is not set");
                            }
                            try {
                                client = new A2aClient(Files.readString(Path.of(tokenFile), StandardCharsets.UTF_8)
                                        .strip());
                            } catch (IOException e) {
                                throw new java.io.UncheckedIOException("cannot read the code model token", e);
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
                });
    }

    @Override
    public Path acm(String projectId, List<CodeFile> files, Path dir) throws IOException {
        ObjectNode input = JsonMapper.shared().createObjectNode();
        input.put("projectId", projectId);
        var current = input.putArray("currentArtifacts");
        files.forEach(f -> current.addObject().put("path", f.path()).put("storagePath", f.storagePath()));
        long deadline = System.nanoTime() + timeout.toNanos();
        A2aClient.TaskSnapshot task;
        try {
            task = calls.submit(url, input, UUID.randomUUID().toString());
            while (!task.terminal()) {
                if (System.nanoTime() > deadline) {
                    throw new IOException("code model plugin did not answer within " + timeout.toSeconds() + " s");
                }
                Thread.sleep(poll.toMillis());
                task = calls.get(url, task.taskId());
            }
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new IOException("interrupted while waiting for the code model plugin", e);
        } catch (RuntimeException e) {
            throw new IOException("code model plugin unavailable at " + url + " (" + e + ")", e);
        }
        if (!"TASK_STATE_COMPLETED".equals(task.state())
                || task.result() == null
                || !task.result().has("acm")) {
            String code = task.error() == null
                    ? task.state()
                    : task.error().path("code").asString(task.state());
            String message =
                    task.error() == null ? "" : task.error().path("message").asString("");
            throw new IOException("code model plugin failed: " + code + ": " + message);
        }
        Path file = dir.resolve(FILE_NAME);
        Files.write(file, JsonMapper.shared().writeValueAsBytes(task.result().get("acm")));
        return file;
    }
}
