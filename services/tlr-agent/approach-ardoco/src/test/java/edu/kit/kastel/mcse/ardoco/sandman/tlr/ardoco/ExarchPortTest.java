package edu.kit.kastel.mcse.ardoco.sandman.tlr.ardoco;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;

import edu.kit.kastel.mcse.ardoco.sandman.agent.ArtifactStore;
import edu.kit.kastel.mcse.ardoco.sandman.agent.Ids;
import edu.kit.kastel.mcse.ardoco.sandman.ardocotext.CorenlpClient;
import edu.kit.kastel.mcse.ardoco.sandman.ardocotext.CorenlpProvider;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.CandidateLink;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.TlrContext;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.TlrInput;
import java.net.ServerSocket;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeSet;
import java.util.concurrent.TimeUnit;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.slf4j.LoggerFactory;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

/**
 * T2.7 port equivalence: the real ArDoCo ExArch informant, fed by a stub of POST /api/chat in a forked JVM, gives the
 * names of the extraction-agent fixture vectors; Transarc on the derived listing gives the SAD-code links of ExArch.
 */
class ExarchPortTest {

    private static final Path FIXTURES = Path.of("../../extraction-agent/tests/fixtures");
    private static final Path GOLDEN = Path.of("../../extraction-agent/tests/golden/benchmark");

    @TempDir
    Path tmp;

    @BeforeAll
    static void startProvider() throws Exception {
        CorenlpProvider.start();
    }

    private List<String> fork(String... args) throws Exception {
        int port;
        try (var socket = new ServerSocket(0)) {
            port = socket.getLocalPort();
        }
        List<String> command = new ArrayList<>(List.of(
                Path.of(System.getProperty("java.home"), "bin", "java").toString(),
                "-cp",
                System.getProperty("java.class.path"),
                ExarchStubDriver.class.getName(),
                args[0],
                String.valueOf(port)));
        command.addAll(List.of(args).subList(1, args.length));
        var builder = new ProcessBuilder(command).redirectErrorStream(true);
        var env = builder.environment();
        env.put("OLLAMA_MODEL_NAME", "stub");
        env.put("OLLAMA_HOST", "http://127.0.0.1:" + port);
        env.put("LLM_CACHE_DIR", Files.createTempDirectory(tmp, "llm-cache").toString());
        for (String name : List.of("SANDMAN_CORENLP_URL", "SANDMAN_CORENLP_TOKEN_FILE", "SANDMAN_CORENLP_USER")) {
            if (System.getProperty(name) != null) {
                env.put(name, System.getProperty(name));
            }
        }
        Process process = builder.start();
        byte[] output = process.getInputStream().readAllBytes();
        assertEquals(true, process.waitFor(60, TimeUnit.SECONDS));
        String text = new String(output, StandardCharsets.UTF_8);
        assertEquals(0, process.exitValue(), text);
        return text.lines().toList();
    }

    @Test
    void javaNamesEqualTheFixtureVectors() throws Exception {
        Path vectors = FIXTURES.resolve("exarch/vectors.json");
        JsonNode expected = JsonMapper.shared().readTree(Files.readString(vectors));
        String result = fork("vectors", vectors.toAbsolutePath().toString()).stream()
                .filter(l -> l.startsWith("RESULT "))
                .findFirst()
                .orElseThrow()
                .substring("RESULT ".length());
        JsonNode actual = JsonMapper.shared().readTree(result);
        assertEquals(16, expected.size());
        assertEquals(expected.size(), actual.size());
        for (int i = 0; i < expected.size(); i++) {
            List<String> names = new ArrayList<>();
            expected.get(i).get("names").forEach(n -> names.add(n.asString()));
            List<String> java = new ArrayList<>();
            actual.get(i).forEach(n -> java.add(n.asString()));
            assertEquals(names, java, "vector " + i);
        }
    }

    @Test
    void transarcOnTheDerivedListingEqualsExarch() throws Exception {
        for (String[] spec :
                new String[][] {{"mediastore", "pcm/ms.repository"}, {"teastore", "pcm/teastore.repository"}}) {
            String project = spec[0];
            Path acm = CodeModels.acm(project + "-code-model");
            Path text = FIXTURES.resolve("benchmark").resolve(project).resolve(project + ".txt");
            JsonNode model = JsonMapper.shared()
                    .readTree(Files.readString(GOLDEN.resolve(project).resolve(spec[1] + ".json")));
            List<String> componentNames = new ArrayList<>();
            model.get("elements").forEach(e -> {
                if ("component".equals(e.get("kind").asString())) {
                    componentNames.add(e.get("name").asString());
                }
            });
            List<String> lines = fork(
                    "exarch",
                    text.toAbsolutePath().toString(),
                    acm.toAbsolutePath().toString(),
                    String.join(",", componentNames));
            Set<String> expected = new TreeSet<>();
            List<String> derived = new ArrayList<>();
            for (String l : lines) {
                if (l.startsWith("LINK ")) {
                    expected.add(l.substring(5));
                } else if (l.startsWith("NAMES ")) {
                    JsonMapper.shared().readTree(l.substring(6)).forEach(n -> derived.add(n.asString()));
                }
            }
            assertFalse(expected.isEmpty(), project);
            assertFalse(derived.isEmpty(), project);

            var store = new ArtifactStore(tmp.resolve("store-" + project));
            var listing = store.put(
                    project,
                    derived.stream()
                            .map(n -> n + "::" + n + "\n")
                            .reduce("", String::concat)
                            .getBytes(StandardCharsets.UTF_8));
            Map<String, String> paths = CodeModels.compilationUnits(acm);
            String docVersion = Ids.stableId("ver", project, "doc");
            String modelVersion = Ids.stableId("ver", project, "listing");
            var versions = new ArrayList<TlrInput.Version>();
            versions.add(new TlrInput.Version(
                    docVersion,
                    Ids.stableId("art", project, "doc"),
                    project + ".txt",
                    "architecture-doc",
                    "markdown",
                    null));
            versions.add(new TlrInput.Version(
                    modelVersion,
                    Ids.stableId("art", project, "listing"),
                    project + ".components",
                    "architecture-model",
                    "component-listing",
                    listing.uri()));
            String uri =
                    store.put(project, "x".getBytes(StandardCharsets.UTF_8)).uri();
            var targets = new ArrayList<TlrInput.Element>();
            for (String path : paths.values()) {
                String version = Ids.stableId("ver", project, path);
                versions.add(
                        new TlrInput.Version(version, Ids.stableId("art", project, path), path, "code", null, uri));
                targets.add(new TlrInput.Element(
                        Ids.stableId("el", version, path),
                        version,
                        "art#" + path,
                        path,
                        "CodeUnit",
                        "file",
                        path,
                        null,
                        null,
                        "code"));
            }
            var sources = new ArrayList<TlrInput.Element>();
            JsonNode doc = JsonMapper.shared()
                    .readTree(Files.readString(GOLDEN.resolve(project).resolve(project + ".txt.json")));
            for (JsonNode e : doc.get("elements")) {
                String key = e.get("localKey").asString();
                sources.add(new TlrInput.Element(
                        Ids.stableId("el", docVersion, key),
                        docVersion,
                        "art#" + key,
                        key,
                        e.get("label").asString(),
                        e.get("kind").asString(),
                        e.get("name").asString(),
                        e.has("text") && !e.get("text").isNull() ? e.get("text").asString() : null,
                        e.has("sentenceIndex") && !e.get("sentenceIndex").isNull()
                                ? e.get("sentenceIndex").asInt()
                                : null,
                        "architecture-doc"));
            }
            for (String n : derived) {
                sources.add(new TlrInput.Element(
                        Ids.stableId("el", modelVersion, n),
                        modelVersion,
                        "m#" + n,
                        n,
                        "Component",
                        "component",
                        n,
                        null,
                        null,
                        "architecture-model"));
            }
            var in = new TlrInput(project, versions, sources, targets);
            var ctx = new TlrContext(
                    null,
                    store,
                    () -> false,
                    LoggerFactory.getLogger(TransarcApproach.class),
                    JsonMapper.shared().createObjectNode());
            TextCache.clear();
            ArdocodeApproach.clearCache();
            List<CandidateLink> links = new TransarcApproach(
                            CorenlpClient.fromEnv(), BenchmarkEquivalenceTest.fakePlugin(acm, new int[1]))
                    .recover(in, ctx);
            Map<String, Integer> sentence = new HashMap<>();
            sources.forEach(e -> sentence.put(e.id(), e.sentenceIndex() == null ? -1 : e.sentenceIndex()));
            Map<String, String> file = new HashMap<>();
            targets.forEach(e -> file.put(e.id(), e.localKey()));
            Set<String> actual = new TreeSet<>();
            links.stream()
                    .filter(l -> "documents".equals(l.linkType()))
                    .forEach(l -> actual.add(sentence.get(l.sourceId()) + " " + file.get(l.targetId())));
            assertEquals(expected, actual, project);
            System.out.printf(
                    "BENCHMARK exarch %s: %d derived components, %d SAD-code links%n",
                    project, derived.size(), actual.size());
        }
    }
}
