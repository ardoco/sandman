package edu.kit.kastel.mcse.ardoco.sandman.agent;

import java.io.IOException;
import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** Content-addressed blobs at root/projectId/sha256; paths derive only from validated ids, never from user paths. */
public final class ArtifactStore {

    private static final Pattern PROJECT = Pattern.compile("[a-z0-9][a-z0-9-]{1,62}");
    private static final Pattern URI = Pattern.compile("artifact://([a-z0-9][a-z0-9-]{1,62})/([0-9a-f]{64})");

    /** Digest and storage URI of a stored blob. */
    public record Stored(String sha256, String uri) {}

    private final Path root;

    public ArtifactStore(Path root) {
        this.root = root;
    }

    /** Stores data atomically; idempotent. */
    public Stored put(String projectId, byte[] data) throws IOException {
        if (!PROJECT.matcher(projectId).matches()) {
            throw new IllegalArgumentException("invalid project id '" + projectId + "'");
        }
        String digest = Ids.sha256Hex(data);
        Path target = root.resolve(projectId).resolve(digest);
        if (!Files.exists(target)) {
            Files.createDirectories(target.getParent());
            Path tmp = Files.createTempFile(target.getParent(), digest, ".tmp");
            try {
                Files.write(tmp, data);
                Files.move(tmp, target, StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING);
            } finally {
                Files.deleteIfExists(tmp);
            }
        }
        return new Stored(digest, "artifact://" + projectId + "/" + digest);
    }

    /** Filesystem path of a storage URI. */
    public Path pathFor(String uri) {
        Matcher m = URI.matcher(uri);
        if (!m.matches()) {
            throw new IllegalArgumentException("invalid artifact uri '" + uri + "'");
        }
        return root.resolve(m.group(1)).resolve(m.group(2));
    }

    public InputStream open(String uri) throws IOException {
        return Files.newInputStream(pathFor(uri));
    }
}
