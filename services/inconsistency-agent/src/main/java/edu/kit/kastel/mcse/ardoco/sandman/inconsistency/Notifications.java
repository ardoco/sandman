package edu.kit.kastel.mcse.ardoco.sandman.inconsistency;

import java.io.IOException;
import java.io.UncheckedIOException;
import java.nio.file.FileAlreadyExistsException;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.nio.file.attribute.PosixFilePermissions;
import java.util.function.Consumer;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import tools.jackson.databind.node.ObjectNode;

/** One structured log line per new or reopened inconsistency: log output and, if configured, a JSONL file. */
final class Notifications implements Consumer<ObjectNode> {

    private static final Logger LOG = LoggerFactory.getLogger("sandman.notification");

    private final Path file;

    Notifications(Path file) {
        this.file = file;
    }

    @Override
    public synchronized void accept(ObjectNode event) {
        String line = event.toString();
        LOG.info(line);
        if (file != null) {
            try {
                create();
                Files.writeString(
                        file,
                        line + "\n",
                        StandardOpenOption.CREATE,
                        StandardOpenOption.APPEND,
                        LinkOption.NOFOLLOW_LINKS);
            } catch (IOException e) {
                throw new UncheckedIOException(e);
            }
        }
    }

    private void create() throws IOException {
        if (Files.isSymbolicLink(file)) {
            throw new IOException("refusing to write through symlink " + file);
        }
        var mode = PosixFilePermissions.fromString("rw-rw----");
        try {
            Files.createFile(file, PosixFilePermissions.asFileAttribute(mode));
            Files.setPosixFilePermissions(file, mode); // createFile is masked by the umask
        } catch (FileAlreadyExistsException e) {
            // already there: keep its mode
        }
    }
}
