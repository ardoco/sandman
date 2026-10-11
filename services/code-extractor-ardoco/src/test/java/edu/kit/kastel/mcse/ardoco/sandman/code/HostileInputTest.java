package edu.kit.kastel.mcse.ardoco.sandman.code;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import edu.kit.kastel.mcse.ardoco.sandman.agent.a2a.SkillException;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.stream.Stream;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import tools.jackson.databind.node.ObjectNode;

/** Staging and the caps: hostile paths, symbolic links, too many files, too many bytes, cleanup. */
class HostileInputTest {

    @TempDir
    Path tmp;

    Path store;
    Path outside;

    @BeforeEach
    void setUp() throws IOException {
        store = Files.createDirectories(tmp.toRealPath().resolve("artifacts"));
        outside = Files.createDirectories(tmp.toRealPath().resolve("outside"));
        Files.writeString(outside.resolve("secret.txt"), "secret");
        Files.writeString(store.resolve("a.txt"), "content");
        Files.createDirectories(store.resolve("sub"));
        Files.writeString(store.resolve("sub/b.txt"), "content b");
    }

    Staging.Item item(String path, String storagePath) {
        return new Staging.Item(path, storagePath);
    }

    SkillException rejected(List<Staging.Item> items, Limits limits) {
        SkillException e = assertThrows(SkillException.class, () -> Staging.stage(store, items, limits));
        assertEquals("INVALID_INPUT", e.code());
        assertFalse(e.retryable());
        return e;
    }

    SkillException rejected(List<Staging.Item> items) {
        return rejected(items, new Limits(100, 1_000_000));
    }

    @Test
    void stagesFilesAtTheirProjectPathsAndDeletesTheDirectory() throws Exception {
        Path root;
        try (Staging staging = Staging.stage(
                store,
                List.of(
                        item("src/a.txt", store.resolve("a.txt").toString()),
                        item("b.txt", store.resolve("sub/b.txt").toString())),
                new Limits(10, 1000))) {
            root = staging.root();
            assertEquals(List.of("b.txt", "src/a.txt"), Fixtures.files(root));
            assertEquals("content", Files.readString(root.resolve("src/a.txt")));
            assertEquals(2, staging.files());
        }
        assertFalse(Files.exists(root));
    }

    @Test
    void mergesTheSameFileListedTwice() throws Exception {
        String a = store.resolve("a.txt").toString();
        try (Staging staging =
                Staging.stage(store, List.of(item("a.txt", a), item("a.txt", a)), new Limits(10, 1000))) {
            assertEquals(1, staging.files());
        }
    }

    @Test
    void rejectsTheSamePathWithDifferentContent() {
        rejected(List.of(
                item("x.txt", store.resolve("a.txt").toString()),
                item("x.txt", store.resolve("sub/b.txt").toString())));
    }

    @Test
    void rejectsStoragePathOutsideTheStore() {
        rejected(List.of(item("x.txt", outside.resolve("secret.txt").toString())));
        rejected(List.of(item("x.txt", "/etc/passwd")));
        rejected(List.of(item("x.txt", store + "/../outside/secret.txt")));
        rejected(List.of(item("x.txt", "a.txt")));
        rejected(List.of(item("x.txt", store.toString())));
    }

    @Test
    void rejectsASymbolicLinkToAFileOutsideTheStore() throws IOException {
        Files.createSymbolicLink(store.resolve("link.txt"), outside.resolve("secret.txt"));
        rejected(List.of(item("x.txt", store.resolve("link.txt").toString())));
    }

    @Test
    void rejectsASymbolicLinkedDirectoryComponent() throws IOException {
        Files.createSymbolicLink(store.resolve("linked"), outside);
        rejected(List.of(item("x.txt", store.resolve("linked/secret.txt").toString())));
    }

    @Test
    void rejectsASymbolicLinkInsideTheStoreToo() throws IOException {
        Files.createSymbolicLink(store.resolve("inside.txt"), store.resolve("a.txt"));
        rejected(List.of(item("x.txt", store.resolve("inside.txt").toString())));
    }

    @Test
    void rejectsDirectoriesAndMissingFiles() {
        rejected(List.of(item("x.txt", store.resolve("sub").toString())));
        rejected(List.of(item("x.txt", store.resolve("missing.txt").toString())));
    }

    @Test
    void rejectsHostileProjectPaths() {
        String a = store.resolve("a.txt").toString();
        for (String path : List.of(
                "../x.txt",
                "a/../../x.txt",
                "/abs/x.txt",
                "a//b.txt",
                "./x.txt",
                "a/./b.txt",
                "a\\b.txt",
                "",
                "dir/",
                "x\0y")) {
            rejected(List.of(item(path, a)));
        }
    }

    @Test
    void rejectsPathsThatConflictAsFileAndDirectory() {
        String a = store.resolve("a.txt").toString();
        String b = store.resolve("sub/b.txt").toString();
        rejected(List.of(item("p", a), item("p/q", b)));
        rejected(List.of(item("p/q", b), item("p", a)));
    }

    @Test
    void rejectsMoreFilesThanTheCap() {
        String a = store.resolve("a.txt").toString();
        String b = store.resolve("sub/b.txt").toString();
        SkillException e = rejected(List.of(item("1", a), item("2", b), item("3", a)), new Limits(2, 1_000_000));
        assertTrue(e.getMessage().contains("too many files"), e.getMessage());
    }

    @Test
    void rejectsMoreBytesThanTheCap() {
        String a = store.resolve("a.txt").toString();
        String b = store.resolve("sub/b.txt").toString();
        SkillException e = rejected(List.of(item("1", a), item("2", b)), new Limits(10, 10));
        assertTrue(e.getMessage().contains("too many bytes"), e.getMessage());
    }

    @Test
    void aRejectedCallLeavesNoStagingDirectory() throws Exception {
        long before = stagingDirs();
        rejected(List.of(
                item("1", store.resolve("a.txt").toString()),
                item("2", store.resolve("missing").toString())));
        rejected(List.of(
                item("p", store.resolve("a.txt").toString()),
                item("p/q", store.resolve("sub/b.txt").toString())));
        assertEquals(before, stagingDirs());
    }

    @Test
    void theSkillInputIsRejectedAsInvalidInputThroughTheService() throws Exception {
        ObjectNode input = Fixtures.extractInput(Fixtures.mappingFixture(), null);
        input.withArray("artifacts")
                .addObject()
                .put("artifactVersionId", "ver_" + "0".repeat(32))
                .put("path", "../escape.java")
                .put("kind", "code")
                .put(
                        "storagePath",
                        Fixtures.mappingFixture().resolve("NoPackage.java").toString())
                .put("contentHash", "0".repeat(64));
        SkillException e = assertThrows(
                SkillException.class,
                () -> Fixtures.service(Fixtures.mappingFixture()).extract(input));
        assertEquals("INVALID_INPUT", e.code());
        CodeService capped = Fixtures.service(Fixtures.mappingFixture());
        capped.limits(new Limits(3, 1_000_000));
        SkillException tooMany = assertThrows(
                SkillException.class, () -> capped.extract(Fixtures.extractInput(Fixtures.mappingFixture(), null)));
        assertEquals("INVALID_INPUT", tooMany.code());
        assertFalse(tooMany.retryable());
    }

    @Test
    void limitsReadTheEnvironment() {
        Limits limits = Limits.fromEnv(java.util.Map.of("SANDMAN_CODE_MAX_FILES", "7", "SANDMAN_CODE_MAX_BYTES", "99"));
        assertEquals(new Limits(7, 99), limits);
        assertEquals(new Limits(20_000, 500L * 1024 * 1024), Limits.fromEnv(java.util.Map.of()));
        assertThrows(
                IllegalArgumentException.class, () -> Limits.fromEnv(java.util.Map.of("SANDMAN_CODE_MAX_FILES", "0")));
        assertThrows(
                IllegalArgumentException.class, () -> Limits.fromEnv(java.util.Map.of("SANDMAN_CODE_MAX_BYTES", "x")));
    }

    private static long stagingDirs() throws IOException {
        try (Stream<Path> s = Files.list(Path.of(System.getProperty("java.io.tmpdir")))) {
            return s.filter(p -> p.getFileName().toString().startsWith("code-extractor-"))
                    .count();
        }
    }
}
