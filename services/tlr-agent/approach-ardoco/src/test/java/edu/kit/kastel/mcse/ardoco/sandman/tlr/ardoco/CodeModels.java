package edu.kit.kastel.mcse.ardoco.sandman.tlr.ardoco;

import java.io.IOException;
import java.io.UncheckedIOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Map;
import java.util.TreeMap;
import org.junit.jupiter.api.Assumptions;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

/** The code models that {@code mvn -Pbenchmark verify} in services/code-extractor-ardoco writes (T2.1b), shared by the tlr-agent tests. */
public final class CodeModels {

    private CodeModels() {}

    /** The ACM file of the name; the calling test is skipped with the reason if it was not written. */
    public static Path acm(String name) {
        Path file = Path.of(System.getProperty("user.home"), ".cache", "sandman", "benchmark-code", name + ".acm");
        Assumptions.assumeTrue(
                Files.isRegularFile(file),
                "code model " + file + " is missing: run mvn -Pbenchmark verify in services/code-extractor-ardoco");
        return file;
    }

    public static JsonNode read(Path acm) {
        try {
            return JsonMapper.shared().readTree(Files.readString(acm));
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }
    }

    /**
     * Compilation units of an ACM: encoded ArDoCo path (as in the CSV of the runner) -> project path. Read from the
     * JSON: ArDoCo's CodeExtractor needs Magika, which this module does not have.
     */
    public static Map<String, String> compilationUnits(Path acm) {
        Map<String, String> paths = new TreeMap<>();
        read(acm).at("/codeItemRepository/repository").forEach(item -> {
            if ("CodeCompilationUnit".equals(item.get("type").asString())) {
                var parts = new ArrayList<String>();
                item.get("pathElements").forEach(e -> parts.add(e.asString()));
                String extension = item.get("extension").asString("");
                parts.add(item.get("name").asString() + (extension.isEmpty() ? "" : "." + extension));
                String encoded = String.join("/", parts);
                int name = encoded.lastIndexOf('/') + 1;
                paths.put(
                        encoded,
                        "JAVA".equals(item.get("language").asString())
                                ? ArdocodeApproach.decode(encoded)
                                : ArdocodeApproach.decode(encoded.substring(0, name)) + encoded.substring(name));
            }
        });
        return paths;
    }
}
