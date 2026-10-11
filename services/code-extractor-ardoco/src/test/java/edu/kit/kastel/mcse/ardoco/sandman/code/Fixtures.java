package edu.kit.kastel.mcse.ardoco.sandman.code;

import edu.kit.kastel.mcse.ardoco.sandman.agent.Contracts;
import edu.kit.kastel.mcse.ardoco.sandman.agent.Settings;
import java.io.IOException;
import java.io.UncheckedIOException;
import java.net.URISyntaxException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Map;
import java.util.stream.Stream;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ArrayNode;
import tools.jackson.databind.node.ObjectNode;

/** Test helpers: an artifact store directory, plugin inputs and the contract validator. */
final class Fixtures {

    static final Contracts CONTRACTS = new Contracts(Settings.defaultContractsDir());

    private Fixtures() {}

    static Path mappingFixture() {
        try {
            return Path.of(Fixtures.class.getResource("/fixtures/mapping").toURI())
                    .toRealPath();
        } catch (URISyntaxException | IOException e) {
            throw new IllegalStateException(e);
        }
    }

    static Settings settings(Path store) {
        return new Settings(
                "code-extractor-ardoco",
                Settings.defaultContractsDir(),
                null,
                null,
                null,
                null,
                null,
                null,
                null,
                null,
                null,
                Map.of(),
                "inbound-token",
                store,
                null,
                null);
    }

    static CodeService service(Path store) {
        CodeService service = new CodeService();
        service.settings = settings(store);
        return service;
    }

    /** Relative paths (slash separated) of all regular files below dir, sorted. */
    static java.util.List<String> files(Path dir) {
        try (Stream<Path> s = Files.walk(dir)) {
            return s.filter(Files::isRegularFile)
                    .map(p -> dir.relativize(p).toString().replace('\\', '/'))
                    .sorted()
                    .toList();
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }
    }

    /** PluginExtractInput for the files of store; tests are the paths under src/test, all others are code. */
    static ObjectNode extractInput(Path store, java.util.List<String> only) {
        ObjectNode input = JsonMapper.shared().createObjectNode();
        input.put("projectId", "mini");
        input.put("llmPolicy", "cloud-allowed");
        ArrayNode artifacts = input.putArray("artifacts");
        ArrayNode current = input.putObject("context").putArray("currentArtifacts");
        for (String path : files(store)) {
            ObjectNode c = current.addObject();
            c.put("path", path);
            c.put("storagePath", store.resolve(path).toString());
            if (only == null || only.contains(path)) {
                ObjectNode a = artifacts.addObject();
                a.put("artifactVersionId", "ver_" + "0".repeat(32));
                a.put("path", path);
                a.put("kind", path.startsWith("src/test/") ? "test" : "code");
                a.put("storagePath", store.resolve(path).toString());
                a.put("contentHash", "0".repeat(64));
            }
        }
        return input;
    }

    static JsonNode validateExtract(JsonNode output) {
        CONTRACTS.validate("skills.schema.json#/$defs/PluginExtractOutput", output);
        return output;
    }
}
