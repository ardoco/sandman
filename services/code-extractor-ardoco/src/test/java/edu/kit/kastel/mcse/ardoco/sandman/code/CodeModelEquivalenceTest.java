package edu.kit.kastel.mcse.ardoco.sandman.code;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import edu.kit.kastel.mcse.ardoco.core.api.models.Metamodel;
import edu.kit.kastel.mcse.ardoco.core.api.models.code.CodeItemRepository;
import edu.kit.kastel.mcse.ardoco.core.common.IdentifierProvider;
import edu.kit.kastel.mcse.ardoco.tlr.models.connectors.generators.code.AllLanguagesExtractor;
import java.io.IOException;
import java.lang.management.ManagementFactory;
import java.lang.management.MemoryPoolMXBean;
import java.lang.management.MemoryType;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.TreeSet;
import java.util.stream.Stream;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

/**
 * T2.6 equivalence: for the benchmark projects the plugin's element set equals ArDoCo's own code model of the same
 * directory, compared as sets of (kind, path, qualified name, start line, end line). The reference side reads the ACM
 * JSON that {@code writeOutCodeModel} writes (the CLI path) and does not use the plugin's mapping code. It downloads
 * the code, so it only runs in profile benchmark ({@code mvn -Pbenchmark verify}).
 */
@Tag("benchmark")
class CodeModelEquivalenceTest {

    @TempDir
    Path tmp;

    private static final JsonMapper JSON = JsonMapper.shared();

    @Test
    void teaStore() throws Exception {
        compare("teastore");
    }

    @Test
    void mediaStore() throws Exception {
        compare("mediastore");
    }

    @Test
    void teammatesExtractsWithinTheContainerHeap() throws Exception {
        Path dir = BenchmarkCode.unpack("teammates", tmp.resolve("teammates"));
        List<String> files = regularFiles(dir);
        CodeService service = Fixtures.service(dir);
        for (MemoryPoolMXBean pool : ManagementFactory.getMemoryPoolMXBeans()) {
            pool.resetPeakUsage();
        }
        long start = System.nanoTime();
        JsonNode output = service.extract(input(dir, files));
        long ms = (System.nanoTime() - start) / 1_000_000;
        long peakHeap = 0;
        for (MemoryPoolMXBean pool : ManagementFactory.getMemoryPoolMXBeans()) {
            if (pool.getType() == MemoryType.HEAP) {
                peakHeap += pool.getPeakUsage().getUsed();
            }
        }
        Fixtures.validateExtract(output);
        System.out.printf(
                "BENCHMARK teammates: %d files staged, %d elements, %d relations, %d ms, peak heap %d MiB (max heap %d MiB)%n",
                files.size(),
                output.get("elements").size(),
                output.get("relations").size(),
                ms,
                peakHeap >> 20,
                Runtime.getRuntime().maxMemory() >> 20);
        assertTrue(output.get("elements").size() > 1000);
    }

    @Test
    void theCodeModelSkillGivesTheAcmOfWriteOutCodeModelInTwoRuns() throws Exception {
        Path dir = BenchmarkCode.unpack("teastore", tmp.resolve("teastore"));
        List<String> files = regularFiles(dir);
        CodeService service = Fixtures.service(dir);
        ObjectNode request = JSON.createObjectNode();
        request.put("projectId", "teastore");
        var current = request.putArray("currentArtifacts");
        files.forEach(p -> current.addObject()
                .put("path", p)
                .put("storagePath", dir.resolve(p).toString()));
        JsonNode first = service.codeModel(request);
        JsonNode second = service.codeModel(request);
        Fixtures.CONTRACTS.validate("skills.schema.json#/$defs/PluginCodeModelOutput", first);
        assertEquals(first, second, "two runs in one JVM");
        assertEquals(written(dir), JSON.writeValueAsString(first.get("acm")), "ACM of the skill, byte for byte");
    }

    /**
     * Writes the {@code code-model} ACM of TeaStore (all files, and without the image service) and MediaStore to the
     * cache, where the tests of the tlr-agent (approach ardocode, T2.1b) read them. The plugin builds them as the
     * skill does for the whole current code, and the size is the one the A2A result carries.
     */
    @Test
    void writesTheCodeModelsForTheTlrAgentTests() throws Exception {
        Files.createDirectories(BenchmarkCode.writtenAcm("x").getParent());
        for (String[] spec : new String[][] {
            {"teastore", "teastore-code-model", ""},
            {"teastore", "teastore-code-model-noimage", "services/tools.descartes.teastore.image/"},
            {"mediastore", "mediastore-code-model", ""},
            {"teammates", "teammates-code-model", ""},
            {"bigbluebutton", "bigbluebutton-code-model", "", "no-media"},
            {"jabref", "jabref-code-model", "", "no-media"}
        }) {
            Path dir = BenchmarkCode.unpack(spec[0], tmp.resolve(spec[1]));
            ObjectNode request = JSON.createObjectNode();
            request.put("projectId", spec[0]);
            var current = request.putArray("currentArtifacts");
            for (String p : regularFiles(dir)) {
                boolean media = spec.length > 3
                        && p.matches("(?i).*\\.(flv|wav|mp[34]|ogg|webm|swf|gif|png|jpe?g|ico|sketch|pdf|zip|jar)$");
                if ((spec[2].isEmpty() || !p.startsWith(spec[2])) && !media) {
                    current.addObject()
                            .put("path", p)
                            .put("storagePath", dir.resolve(p).toString());
                }
            }
            JsonNode acm = Fixtures.service(dir).codeModel(request).get("acm");
            byte[] bytes = JSON.writeValueAsBytes(acm);
            Files.write(BenchmarkCode.writtenAcm(spec[1]), bytes);
            System.out.printf("BENCHMARK %s: %d files sent, ACM %d bytes%n", spec[1], current.size(), bytes.length);
        }
    }

    private void compare(String name) throws Exception {
        Path dir = BenchmarkCode.unpack(name, tmp.resolve(name));
        List<String> files = regularFiles(dir);
        long start = System.nanoTime();
        JsonNode output = Fixtures.service(dir).extract(input(dir, files));
        long pluginMs = (System.nanoTime() - start) / 1_000_000;
        Fixtures.validateExtract(output);

        var unrecognized = new TreeSet<String>();
        output.get("warnings")
                .forEach(w ->
                        unrecognized.add(w.asString().substring(0, w.asString().indexOf(':'))));
        List<String> plugin = new ArrayList<>();
        for (JsonNode e : output.get("elements")) {
            if (unrecognized.contains(e.get("path").asString())) {
                continue; // file element only, with a warning: ArDoCo's model has no item for it
            }
            String kind = e.get("kind").asString().replace("test-", "").replace("class", "type");
            plugin.add(tuple(
                    kind,
                    e.get("path").asString(),
                    e.path("qualifiedName").asString(e.get("name").asString()),
                    e.path("startLine").asInt(0),
                    e.path("endLine").asInt(0)));
        }
        String acm = written(dir);
        List<String> reference = referenceTuples(JSON.readTree(acm));

        assertNoDuplicates(plugin, "plugin " + name);
        assertNoDuplicates(reference, "ArDoCo " + name);
        var onlyPlugin = new TreeSet<>(plugin);
        onlyPlugin.removeAll(reference);
        var onlyArdoco = new TreeSet<>(reference);
        onlyArdoco.removeAll(plugin);
        System.out.printf(
                "BENCHMARK %s: %d files, plugin %d elements (%d ms, %d files without code items, of them .java or .sh: %s), ArDoCo %d; only plugin %d, only ArDoCo %d%n",
                name,
                files.size(),
                plugin.size(),
                pluginMs,
                unrecognized.size(),
                unrecognized.stream()
                        .filter(p -> p.endsWith(".java") || p.endsWith(".sh"))
                        .toList(),
                reference.size(),
                onlyPlugin.size(),
                onlyArdoco.size());
        assertEquals(List.of(), onlyPlugin.stream().limit(10).toList(), name + ": only in the plugin");
        assertEquals(List.of(), onlyArdoco.stream().limit(10).toList(), name + ": only in ArDoCo");
        assertEquals(new TreeSet<>(reference), new TreeSet<>(plugin));
        assertTrue(count(plugin, "file") > 0 && count(plugin, "type") > 0 && count(plugin, "method") > 0);
        report(name, plugin);
    }

    /** Differences to the ACM committed in the Benchmark (older extractor, no lines): reported, never asserted. */
    private static void report(String name, List<String> plugin) {
        try {
            Path file = BenchmarkCode.file("acm", name);
            JsonNode committed = JSON.readTree(Files.readString(file));
            Map<String, JsonNode> repo = new HashMap<>();
            committed
                    .get("codeItemRepository")
                    .get("repository")
                    .properties()
                    .forEach(e -> repo.put(e.getKey(), e.getValue()));
            var committedFiles = new TreeSet<String>();
            var committedTypes = new TreeSet<String>();
            for (JsonNode item : repo.values()) {
                String type = item.get("type").asString();
                if (type.equals("CodeCompilationUnit")) {
                    committedFiles.add(path(item));
                } else if (type.equals("ClassUnit") || type.equals("InterfaceUnit")) {
                    committedTypes.add(
                            path(repo.get(item.get("compilationUnitId").asString())) + "|"
                                    + item.get("name").asString());
                }
            }
            var pluginFiles = new TreeSet<String>();
            var pluginTypes = new TreeSet<String>();
            for (String t : plugin) {
                String[] f = t.split("\\|", -1);
                if (f[0].equals("file")) {
                    pluginFiles.add(f[1]);
                } else if (f[0].equals("type")) {
                    pluginTypes.add(f[1] + "|" + f[2].substring(f[2].lastIndexOf('.') + 1));
                }
            }
            var onlyPluginFiles = new TreeSet<>(pluginFiles);
            onlyPluginFiles.removeIf(p -> committedFiles.stream().anyMatch(c -> c.endsWith("/" + p) || c.equals(p)));
            var onlyCommittedFiles = new TreeSet<>(committedFiles);
            onlyCommittedFiles.removeIf(p -> pluginFiles.stream().anyMatch(c -> p.endsWith("/" + c) || p.equals(c)));
            System.out.printf(
                    "BENCHMARK %s vs committed ACM (%s): files plugin %d / committed %d, files only in plugin %d, only in committed %d; types plugin %d / committed %d%n",
                    name,
                    file.getFileName(),
                    pluginFiles.size(),
                    committedFiles.size(),
                    onlyPluginFiles.size(),
                    onlyCommittedFiles.size(),
                    pluginTypes.size(),
                    committedTypes.size());
            onlyPluginFiles.stream().limit(5).forEach(p -> System.out.println("BENCHMARK   only in plugin: " + p));
            onlyCommittedFiles.stream()
                    .limit(5)
                    .forEach(p -> System.out.println("BENCHMARK   only in committed: " + p));
        } catch (IOException | RuntimeException e) {
            System.out.println("BENCHMARK " + name + ": no comparison with the committed ACM: " + e);
        }
    }

    private static String path(JsonNode cu) {
        List<String> parts = new ArrayList<>();
        cu.get("pathElements").forEach(p -> parts.add(p.asString()));
        String ext = cu.path("extension").asString("");
        parts.add(cu.get("name").asString() + (ext.isEmpty() ? "" : "." + ext));
        return ElementMapping.decode(String.join("/", parts));
    }

    /** Tuples of the ACM JSON, derived without ElementMapping: each method counts for the innermost type listing it. */
    static List<String> referenceTuples(JsonNode acm) {
        Map<String, JsonNode> repo = new HashMap<>();
        acm.get("codeItemRepository").get("repository").properties().forEach(e -> repo.put(e.getKey(), e.getValue()));
        List<String> tuples = new ArrayList<>();
        Map<String, List<JsonNode>> typesOfUnit = new HashMap<>();
        Map<String, String> packageOfUnit = new HashMap<>();
        for (JsonNode item : repo.values()) {
            String type = item.get("type").asString();
            if (type.equals("CodeCompilationUnit")) {
                List<String> names = new ArrayList<>();
                for (JsonNode p = item; p.hasNonNull("parentId"); ) {
                    p = repo.get(p.get("parentId").asString());
                    if (p.get("type").asString().equals("CodePackage")) {
                        names.add(0, p.get("name").asString());
                    }
                }
                String pkg = names.isEmpty() ? "" : String.join(".", names) + ".";
                packageOfUnit.put(item.get("id").asString(), pkg);
                String fileName = path(item).substring(path(item).lastIndexOf('/') + 1);
                tuples.add(tuple("file", path(item), pkg + fileName, 0, 0));
            } else if (type.equals("ClassUnit") || type.equals("InterfaceUnit")) {
                typesOfUnit
                        .computeIfAbsent(item.get("compilationUnitId").asString(), k -> new ArrayList<>())
                        .add(item);
            }
        }
        for (var entry : typesOfUnit.entrySet()) {
            JsonNode unit = repo.get(entry.getKey());
            String pkg = packageOfUnit.get(entry.getKey());
            Map<String, String> qualified = new HashMap<>();
            for (JsonNode t : entry.getValue()) {
                List<String> chain = new ArrayList<>();
                for (JsonNode c = t;
                        c != null;
                        c = c.hasNonNull("parentDatatypeId")
                                ? repo.get(c.get("parentDatatypeId").asString())
                                : null) {
                    chain.add(0, c.get("name").asString());
                }
                qualified.put(t.get("id").asString(), pkg + String.join(".", chain));
                tuples.add(tuple(
                        "type",
                        path(unit),
                        qualified.get(t.get("id").asString()),
                        t.get("startLine").asInt(),
                        t.get("endLine").asInt()));
            }
            Map<String, List<JsonNode>> listedBy = new HashMap<>();
            for (JsonNode t : entry.getValue()) {
                for (JsonNode id : t.get("content")) {
                    JsonNode m = repo.get(id.asString());
                    if ("ControlElement".equals(m.get("type").asString())) {
                        listedBy.computeIfAbsent(
                                        m.get("name").asString() + "|"
                                                + m.get("startLine").asInt() + "|"
                                                + m.get("endLine").asInt(),
                                        k -> new ArrayList<>())
                                .add(t);
                    }
                }
            }
            for (var listed : listedBy.entrySet()) {
                JsonNode owner = listed.getValue().stream()
                        .min((a, b) -> {
                            int sizeA = a.get("endLine").asInt()
                                    - a.get("startLine").asInt();
                            int sizeB = b.get("endLine").asInt()
                                    - b.get("startLine").asInt();
                            return sizeA != sizeB
                                    ? Integer.compare(sizeA, sizeB)
                                    : Integer.compare(depth(b, repo), depth(a, repo));
                        })
                        .orElseThrow();
                String[] key = listed.getKey().split("\\|");
                long copies = listed.getValue().stream().filter(t -> t == owner).count();
                for (long i = 0; i < copies; i++) {
                    tuples.add(tuple(
                            "method",
                            path(unit),
                            qualified.get(owner.get("id").asString()) + "#" + key[0],
                            Integer.parseInt(key[1]),
                            Integer.parseInt(key[2])));
                }
            }
        }
        return tuples;
    }

    private static int depth(JsonNode type, Map<String, JsonNode> repo) {
        int depth = 0;
        for (JsonNode t = type;
                t.hasNonNull("parentDatatypeId");
                t = repo.get(t.get("parentDatatypeId").asString())) {
            depth++;
        }
        return depth;
    }

    /** A line ArDoCo could not determine (-1) is an absent line (0); an end before the start is absent. */
    private static String tuple(String kind, String path, String qualifiedName, int start, int end) {
        int s = Math.max(start, 0);
        return kind + "|" + path + "|" + qualifiedName + "|" + s + "|" + (s > 0 && end >= s ? end : 0);
    }

    private static long count(List<String> tuples, String kind) {
        return tuples.stream().filter(t -> t.startsWith(kind + "|")).count();
    }

    private static void assertNoDuplicates(List<String> tuples, String what) {
        var seen = new TreeSet<String>();
        var duplicates = tuples.stream().filter(t -> !seen.add(t)).limit(10).toList();
        assertEquals(List.of(), duplicates, what + ": each element once");
    }

    private static String written(Path dir) throws IOException {
        IdentifierProvider.reset();
        var extractor = new AllLanguagesExtractor(
                new CodeItemRepository(), dir.toString(), Metamodel.CODE_WITH_COMPILATION_UNITS_AND_PACKAGES);
        Path file = Files.createTempFile("acm-", ".json");
        try {
            extractor.writeOutCodeModel(extractor.extractModel(), file.toFile());
            return Files.readString(file);
        } finally {
            Files.deleteIfExists(file);
        }
    }

    /** The files of a call: regular files below the per-file cap (the archives and jars of TeaStore are above it). */
    private static List<String> regularFiles(Path dir) throws IOException {
        try (Stream<Path> s = Files.walk(dir)) {
            return s.filter(p -> Files.isRegularFile(p, java.nio.file.LinkOption.NOFOLLOW_LINKS)
                            && size(p) <= Limits.DEFAULT_FILE_BYTES)
                    .map(p -> dir.relativize(p).toString().replace('\\', '/'))
                    .sorted()
                    .toList();
        }
    }

    private static long size(Path p) {
        try {
            return Files.size(p);
        } catch (IOException e) {
            throw new java.io.UncheckedIOException(e);
        }
    }

    private static ObjectNode input(Path dir, List<String> files) {
        ObjectNode input = JSON.createObjectNode();
        input.put("projectId", "bench");
        input.put("llmPolicy", "cloud-allowed");
        var artifacts = input.putArray("artifacts");
        var current = input.putObject("context").putArray("currentArtifacts");
        for (String path : files) {
            current.addObject()
                    .put("path", path)
                    .put("storagePath", dir.resolve(path).toString());
            artifacts
                    .addObject()
                    .put("artifactVersionId", "ver_" + "0".repeat(32))
                    .put("path", path)
                    .put("kind", path.contains("/src/test/") || path.startsWith("src/test/") ? "test" : "code")
                    .put("storagePath", dir.resolve(path).toString())
                    .put("contentHash", "0".repeat(64));
        }
        return input;
    }
}
