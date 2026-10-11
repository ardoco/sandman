package edu.kit.kastel.mcse.ardoco.sandman.code;

import edu.kit.kastel.mcse.ardoco.sandman.agent.a2a.SkillException;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.io.UncheckedIOException;
import java.nio.file.FileVisitResult;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.nio.file.SimpleFileVisitor;
import java.nio.file.attribute.BasicFileAttributes;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Copies the files of a call into a fresh temporary directory at their project paths, so that ArDoCo sees a plain
 * source tree. Customer input is untrusted: every {@code storagePath} must be a regular file below the artifact
 * store reached without a symbolic link, every project path must be relative and stay below the directory, and the
 * file count and byte caps apply. Nothing is read outside the store and nothing is written outside the directory.
 */
final class Staging implements AutoCloseable {

    /** One file of the call: project path and absolute path in the artifact store. */
    record Item(String path, String storagePath) {}

    private static final int MAX_PATH_LENGTH = 4096;

    private final Path root;
    private final int files;

    private Staging(Path root, int files) {
        this.root = root;
        this.files = files;
    }

    Path root() {
        return root;
    }

    int files() {
        return files;
    }

    /** Validates and copies {@code items} (duplicates with the same storagePath are merged); INVALID_INPUT otherwise. */
    static Staging stage(Path artifactStore, List<Item> items, Limits limits) throws SkillException {
        Path lexical = artifactStore.toAbsolutePath().normalize();
        Path real;
        try {
            real = lexical.toRealPath();
        } catch (IOException e) {
            throw new UncheckedIOException("artifact store not readable: " + lexical, e);
        }
        Map<String, Item> unique = new LinkedHashMap<>();
        for (Item item : items) {
            validateProjectPath(item.path());
            Item previous = unique.putIfAbsent(item.path(), item);
            if (previous != null && !previous.storagePath().equals(item.storagePath())) {
                throw invalid("duplicate path with different content: " + item.path());
            }
        }
        if (unique.size() > limits.maxFiles()) {
            throw invalid("too many files: " + unique.size() + " > " + limits.maxFiles());
        }
        List<Path> sources = new ArrayList<>();
        long total = 0;
        for (Item item : unique.values()) {
            Path source = storedFile(lexical, real, item.storagePath());
            try {
                long size = Files.size(source);
                if (size > limits.maxFileBytes()) {
                    throw invalid("file too large: " + printable(item.path()) + " has more than "
                            + limits.maxFileBytes() + " bytes");
                }
                total += size;
            } catch (IOException e) {
                throw invalid("cannot read " + item.storagePath() + ": "
                        + e.getClass().getSimpleName());
            }
            if (total > limits.maxBytes()) {
                throw invalid("too many bytes: more than " + limits.maxBytes());
            }
            sources.add(source);
        }
        Path dir;
        try {
            dir = Files.createTempDirectory("code-extractor-");
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }
        Staging staging = new Staging(dir, unique.size());
        try {
            long copied = 0;
            int i = 0;
            for (Item item : unique.values()) {
                Path target = dir.resolve(item.path()).normalize();
                if (!target.startsWith(dir) || target.equals(dir)) {
                    throw invalid("path leaves the extraction directory: " + item.path());
                }
                try {
                    Files.createDirectories(target.getParent());
                    try (InputStream in = Files.newInputStream(sources.get(i++), LinkOption.NOFOLLOW_LINKS)) {
                        copied += copyAtMost(in, target, Math.min(limits.maxFileBytes(), limits.maxBytes() - copied));
                    }
                } catch (java.nio.file.FileAlreadyExistsException
                        | java.nio.file.NotDirectoryException
                        | java.nio.file.DirectoryNotEmptyException e) {
                    throw invalid("path conflicts with another path: " + item.path());
                } catch (IOException e) {
                    throw invalid(
                            "cannot stage " + item.path() + ": " + e.getClass().getSimpleName());
                }
            }
            return staging;
        } catch (SkillException | RuntimeException e) {
            staging.close();
            throw e;
        }
    }

    /** Copies at most {@code remaining} bytes; fails before writing the first byte past the budget. */
    static long copyAtMost(InputStream in, Path target, long remaining) throws IOException, SkillException {
        long size = 0;
        byte[] buffer = new byte[64 * 1024];
        try (OutputStream out = Files.newOutputStream(target)) {
            for (int n; (n = in.read(buffer)) >= 0; ) {
                if (n > remaining - size) {
                    throw invalid("too many bytes: file grew while staging");
                }
                out.write(buffer, 0, n);
                size += n;
            }
        }
        return size;
    }

    static void validateProjectPath(String path) throws SkillException {
        if (path == null || path.isEmpty() || path.length() > MAX_PATH_LENGTH) {
            throw invalid("invalid path length");
        }
        if (path.startsWith("/") || path.indexOf('\\') >= 0 || path.indexOf('\0') >= 0) {
            throw invalid("path must be relative, without backslash or NUL: " + printable(path));
        }
        for (String segment : path.split("/", -1)) {
            if (segment.isEmpty() || segment.equals(".") || segment.equals("..")) {
                throw invalid("path has an empty, '.' or '..' segment: " + printable(path));
            }
        }
    }

    /** The regular file at storagePath, reached from the store root without any symbolic link. */
    static Path storedFile(Path lexicalStore, Path realStore, String storagePath) throws SkillException {
        if (storagePath == null || storagePath.indexOf('\0') >= 0) {
            throw invalid("invalid storagePath");
        }
        Path given = Path.of(storagePath);
        if (!given.isAbsolute() || !given.normalize().equals(given)) {
            throw invalid("storagePath must be absolute and normalized: " + printable(storagePath));
        }
        Path relative = given.startsWith(lexicalStore)
                ? lexicalStore.relativize(given)
                : given.startsWith(realStore) ? realStore.relativize(given) : null;
        if (relative == null || relative.toString().isEmpty()) {
            throw invalid("storagePath is outside the artifact store: " + printable(storagePath));
        }
        Path current = realStore;
        for (Path part : relative) {
            current = current.resolve(part);
            if (Files.isSymbolicLink(current)) {
                throw invalid("storagePath contains a symbolic link: " + printable(storagePath));
            }
        }
        if (!Files.isRegularFile(current, LinkOption.NOFOLLOW_LINKS)) {
            throw invalid("storagePath is not a regular file: " + printable(storagePath));
        }
        if (linkCount(current) > 1) {
            throw invalid("storagePath is a hard link: " + printable(storagePath));
        }
        return current;
    }

    /** Hard links could alias a file outside the store; platforms without unix attributes report 1. */
    private static int linkCount(Path file) {
        try {
            return (Integer) Files.getAttribute(file, "unix:nlink", LinkOption.NOFOLLOW_LINKS);
        } catch (UnsupportedOperationException | IllegalArgumentException | IOException e) {
            return 1;
        }
    }

    private static String printable(String text) {
        String cut = text.length() > 200 ? text.substring(0, 200) + "..." : text;
        return cut.replaceAll("\\p{Cntrl}", "?");
    }

    private static SkillException invalid(String message) {
        return new SkillException("INVALID_INPUT", message, false);
    }

    @Override
    public void close() {
        try {
            Files.walkFileTree(root, new SimpleFileVisitor<>() {
                @Override
                public FileVisitResult visitFile(Path file, BasicFileAttributes attrs) throws IOException {
                    Files.delete(file);
                    return FileVisitResult.CONTINUE;
                }

                @Override
                public FileVisitResult postVisitDirectory(Path dir, IOException exc) throws IOException {
                    Files.delete(dir);
                    return FileVisitResult.CONTINUE;
                }
            });
        } catch (IOException e) {
            throw new UncheckedIOException("cannot delete " + root, e);
        }
    }
}
