package edu.kit.kastel.mcse.ardoco.sandman.inconsistency;

import java.io.IOException;
import java.io.UncheckedIOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
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
                Files.writeString(file, line + "\n", StandardOpenOption.CREATE, StandardOpenOption.APPEND);
            } catch (IOException e) {
                throw new UncheckedIOException(e);
            }
        }
    }
}
