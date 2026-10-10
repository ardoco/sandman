package edu.kit.kastel.mcse.ardoco.sandman.inconsistency;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import java.io.UncheckedIOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.attribute.PosixFilePermissions;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

class NotificationsTest {

    private static final ObjectNode EVENT =
            JsonMapper.shared().createObjectNode().put("id", "i");

    @Test
    void fileIsCreatedGroupWritableRegardlessOfUmask(@TempDir Path dir) throws Exception {
        Path file = dir.resolve("sandman.jsonl");
        new Notifications(file).accept(EVENT);
        assertEquals("rw-rw----", PosixFilePermissions.toString(Files.getPosixFilePermissions(file)));
        assertEquals("{\"id\":\"i\"}\n", Files.readString(file));
    }

    @Test
    void symlinkIsRefused(@TempDir Path dir) throws Exception {
        Path target = Files.createFile(dir.resolve("target"));
        Path link = Files.createSymbolicLink(dir.resolve("sandman.jsonl"), target);
        assertThrows(UncheckedIOException.class, () -> new Notifications(link).accept(EVENT));
        assertEquals("", Files.readString(target));
    }
}
