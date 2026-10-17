package edu.kit.kastel.mcse.ardoco.sandman.tlr.ardoco;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import edu.kit.kastel.mcse.ardoco.core.api.PreprocessingData;
import edu.kit.kastel.mcse.ardoco.core.api.models.ModelFormat;
import edu.kit.kastel.mcse.ardoco.core.api.output.ArdocoResult;
import edu.kit.kastel.mcse.ardoco.core.common.util.DataRepositoryHelper;
import edu.kit.kastel.mcse.ardoco.core.textproviderjson.dto.TextDto;
import edu.kit.kastel.mcse.ardoco.sandman.agent.ArtifactStore;
import edu.kit.kastel.mcse.ardoco.sandman.agent.Ids;
import edu.kit.kastel.mcse.ardoco.sandman.agent.a2a.A2aClient;
import edu.kit.kastel.mcse.ardoco.sandman.ardocotext.CorenlpClient;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.CandidateLink;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.TlrContext;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.TlrInput;
import edu.kit.kastel.mcse.ardoco.tlr.execution.Ardocode;
import edu.kit.kastel.mcse.ardoco.tlr.execution.Swattr;
import edu.kit.kastel.mcse.ardoco.tlr.models.agents.ArchitectureConfiguration;
import edu.kit.kastel.mcse.ardoco.tlr.models.agents.CodeConfiguration;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Collection;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeSet;
import org.eclipse.collections.api.factory.SortedMaps;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.slf4j.LoggerFactory;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

/**
 * M2 exit: the adapter (KG rendering, sentence split checked against the lines) yields the link set of the standalone
 * ArDoCo runner on the raw benchmark text, as (1-based sentence, model id) pairs. Both sides take their annotation from
 * the CoreNLP provider service (container of the compose profile corenlp-local); the standalone side sends the raw text
 * without eolonly and converts the answer like ArDoCo's TextProcessorService. Projects whose raw text CoreNLP splits
 * into more sentences than lines are reported as excluded, except the required cases.
 */
class BenchmarkEquivalenceTest {

    private static final Path FIXTURES = Path.of("../../extraction-agent/tests/fixtures/benchmark");
    private static final Path GOLDEN = Path.of("../../extraction-agent/tests/golden/benchmark");

    private record Case(String project, String text, String model, String format, boolean required) {
        String name() {
            return project + "/" + model;
        }
    }

    private static final List<Case> CASES = List.of(
            new Case("mediastore", "mediastore.txt", "pcm/ms.repository", "pcm-repository", true),
            new Case("mediastore", "mediastore.txt", "uml/ms.uml", "uml", true),
            new Case("teastore", "teastore.txt", "pcm/teastore.repository", "pcm-repository", true),
            new Case("teastore", "teastore.txt", "uml/teastore.uml", "uml", false),
            new Case("teammates", "teammates.txt", "pcm/teammates.repository", "pcm-repository", false),
            new Case("teammates", "teammates.txt", "uml/teammates.uml", "uml", false),
            new Case("bigbluebutton", "bigbluebutton.txt", "pcm/bbb.repository", "pcm-repository", false),
            new Case("bigbluebutton", "bigbluebutton.txt", "uml/bbb.uml", "uml", false),
            new Case("jabref", "jabref.txt", "pcm/jabref.repository", "pcm-repository", false),
            new Case("jabref", "jabref.txt", "uml/jabref.uml", "uml", false));

    @TempDir
    Path tmp;

    @BeforeAll
    static void startProvider() throws Exception {
        CorenlpProvider.start();
    }

    @AfterAll
    static void reportMemory() throws Exception {
        System.out.println("BENCHMARK provider peak memory " + CorenlpProvider.peakMemoryMiB() + " MiB");
    }

    @Test
    void adapterEqualsStandaloneRunner() throws Exception {
        List<String> excluded = new ArrayList<>();
        for (Case c : CASES) {
            long start = System.nanoTime();
            Set<String> standalone = standalone(c);
            long standaloneMs = (System.nanoTime() - start) / 1_000_000;
            TlrInput in = input(c);
            long lines =
                    in.sources().stream().filter(e -> e.sentenceIndex() != null).count();
            long split = standaloneSentences;
            if (split != lines) {
                String msg =
                        c.name() + ": raw text splits into " + split + " sentences, rendering has " + lines + " lines";
                assertTrue(!c.required(), "required case excluded: " + msg);
                excluded.add(msg);
                continue;
            }
            start = System.nanoTime();
            Set<String> adapter = adapter(in);
            System.out.printf(
                    "BENCHMARK %s: %d sentences, %d links, standalone %d ms, adapter %d ms%n",
                    c.name(), lines, adapter.size(), standaloneMs, (System.nanoTime() - start) / 1_000_000);
            writeCsv(c, "standalone", standalone);
            writeCsv(c, "adapter", adapter);
            assertEquals(standalone, adapter, c.name());
            if (c.required()) {
                assertTrue(!adapter.isEmpty(), c.name());
            }
        }
        excluded.forEach(e -> System.out.println("BENCHMARK EXCLUDED " + e));
    }

    @Test
    void oneAnnotationServesAllModelsOfADocumentVersion() throws Exception {
        TextCache.clear(); // an earlier test may have annotated this text
        Case pcm = CASES.get(0);
        Case uml = CASES.get(1);
        Set<String> expected = standalone(pcm);
        expected.addAll(standalone(uml));
        TlrInput a = input(pcm);
        TlrInput b = input(uml);
        var versions = new ArrayList<>(a.versions());
        versions.add(b.versions().get(1));
        var targets = new ArrayList<>(a.targets());
        targets.addAll(b.targets());
        var both = new TlrInput(a.projectId(), versions, a.sources(), targets);
        int[] requests = {0};
        var counting =
                new CorenlpClient(
                        java.net.URI.create(CorenlpProvider.start()),
                        java.time.Duration.ofSeconds(300),
                        CorenlpClient.Eolonly.AUTO,
                        CorenlpClient.BACKOFF,
                        CorenlpClient.fromEnv().authorization()) {
                    @Override
                    public TextDto request(String text, boolean eolonly, java.util.function.BooleanSupplier cancelled) {
                        requests[0]++;
                        return super.request(text, eolonly, cancelled);
                    }
                };
        var ctx = new TlrContext(
                null,
                store,
                () -> false,
                LoggerFactory.getLogger(ArdocoApproach.class),
                JsonMapper.shared().createObjectNode());
        List<CandidateLink> links = new ArdocoApproach(counting).recover(both, ctx);
        assertEquals(1, requests[0], "one annotation request for one document version and two models");
        Map<String, Integer> sentence = new java.util.HashMap<>();
        both.sources().forEach(e -> sentence.put(e.id(), e.sentenceIndex() == null ? -1 : e.sentenceIndex() + 1));
        Map<String, String> model = new java.util.HashMap<>();
        both.targets().forEach(e -> model.put(e.id(), e.localKey()));
        Set<String> actual = new TreeSet<>();
        links.forEach(l -> actual.add(sentence.get(l.sourceId()) + " " + model.get(l.targetId())));
        assertEquals(expected, actual);
    }

    @Test
    void unknownModelIdAndUnsupportedFormat() throws Exception {
        Case c = CASES.get(0);
        TlrInput in = input(c);
        var ctx = new TlrContext(
                null,
                store,
                () -> false,
                LoggerFactory.getLogger(ArdocoApproach.class),
                JsonMapper.shared().createObjectNode());
        var withoutModelElements = new TlrInput(
                in.projectId(),
                in.versions(),
                in.sources(),
                in.targets().stream().filter(e -> !"component".equals(e.kind())).toList());
        var e = org.junit.jupiter.api.Assertions.assertThrows(
                IllegalStateException.class, () -> new ArdocoApproach().recover(withoutModelElements, ctx));
        assertTrue(e.getMessage().contains("unknown model id"), e.getMessage());

        var wrongFormat = new TlrInput(
                in.projectId(),
                in.versions().stream()
                        .map(v -> "architecture-model".equals(v.kind())
                                ? new TlrInput.Version(
                                        v.id(), v.artifactId(), v.path(), v.kind(), "acm", v.storageUri())
                                : v)
                        .toList(),
                in.sources(),
                in.targets());
        assertEquals(List.of(), new ArdocoApproach().recover(wrongFormat, ctx));

        // no format on the artifact (kind rules without format): the suffix decides, as in the extraction-agent
        var noFormat = new TlrInput(
                in.projectId(),
                in.versions().stream()
                        .map(v -> "architecture-model".equals(v.kind())
                                ? new TlrInput.Version(v.id(), v.artifactId(), v.path(), v.kind(), null, v.storageUri())
                                : v)
                        .toList(),
                in.sources(),
                in.targets());
        assertEquals(new ArdocoApproach().recover(in, ctx), new ArdocoApproach().recover(noFormat, ctx));
        assertEquals(
                ModelFormat.UML,
                ArdocoApproach.format(new TlrInput.Version("v", "a", "x/M.XMI", "architecture-model", null, null)));
        assertEquals(
                null,
                ArdocoApproach.format(new TlrInput.Version("v", "a", "x/m.json", "architecture-model", null, null)));
    }

    // --- ardocode (T2.1b): SAD to code over the code model of the plugin

    /** The plugin's answer is the ACM: a fake of the A2A calls. */
    static CodeModelClient fakePlugin(Path acm, int[] calls) {
        JsonNode node = JsonMapper.shared().readTree(readString(acm));
        return new CodeModelClient(
                "http://plugin",
                java.time.Duration.ofSeconds(5),
                java.time.Duration.ofMillis(1),
                new CodeModelClient.Calls() {
                    @Override
                    public String identity(String url) {
                        return "code-extractor-ardoco@test";
                    }

                    @Override
                    public A2aClient.TaskSnapshot submit(String url, JsonNode input, String messageId) {
                        calls[0]++;
                        return new A2aClient.TaskSnapshot(
                                "t",
                                "TASK_STATE_COMPLETED",
                                JsonMapper.shared().createObjectNode().set("acm", node),
                                null);
                    }

                    @Override
                    public A2aClient.TaskSnapshot get(String url, String taskId) {
                        throw new IllegalStateException();
                    }

                    @Override
                    public A2aClient.TaskSnapshot cancel(String url, String taskId) {
                        throw new IllegalStateException();
                    }
                });
    }

    private static String readString(Path file) {
        try {
            return Files.readString(file);
        } catch (IOException e) {
            throw new java.io.UncheckedIOException(e);
        }
    }

    /** Standalone Ardocode on the raw text and the ACM; its SAD-code CSV as (1-based sentence, project path). */
    private Set<String> standaloneArdocode(Case c, Path acm, Map<String, String> paths) throws IOException {
        Path out = Files.createDirectories(tmp.resolve("ardocode-" + c.project() + "-" + acm.getFileName()));
        Path text = FIXTURES.resolve(c.project()).resolve(c.text());
        var annotated =
                CorenlpClient.convert(CorenlpClient.fromEnv().request(Files.readString(text), false, () -> false));
        standaloneSentences = annotated.getSentences().size();
        var runner = new Ardocode("bench");
        runner.setUp(
                text.toFile(),
                new CodeConfiguration(acm.toFile(), CodeConfiguration.CodeConfigurationType.ACM_FILE),
                SortedMaps.immutable.empty(),
                out.toFile());
        DataRepositoryHelper.putPreprocessingData(
                runner.getArdoco().getDataRepository(), new PreprocessingData(annotated));
        runner.run();
        Path csv;
        try (var files = Files.list(out)) {
            csv = files.filter(f -> f.getFileName().toString().matches("sadCodeTlr_.*\\.csv"))
                    .findFirst()
                    .orElseThrow(() -> new AssertionError("no SAD-code CSV in " + out + ": "
                            + java.util.Arrays.toString(out.toFile().list())));
        }
        Set<String> links = new TreeSet<>();
        for (String line : readString(csv).split("\n")) {
            if (line.isBlank() || !Character.isDigit(line.charAt(0))) { // header: modelElementID,codeId
                continue;
            }
            assertTrue(line.matches("\\d+,.+"), "CSV line " + line);
            int comma = line.indexOf(',');
            String encoded = line.substring(comma + 1).strip();
            String path = paths.get(encoded);
            assertTrue(path != null, "CSV path " + encoded + " is not a compilation unit of the ACM");
            links.add(line.substring(0, comma).strip() + " " + path);
        }
        return links;
    }

    /** The KG view: sentences of the golden, one code version and one file element per compilation unit. */
    private TlrInput ardocodeInput(Case c, Collection<String> paths) throws IOException {
        store = new ArtifactStore(tmp.resolve("store"));
        var versions = new ArrayList<TlrInput.Version>();
        String docVersion = Ids.stableId("ver", c.project(), "doc");
        versions.add(new TlrInput.Version(
                docVersion, Ids.stableId("art", c.project(), "doc"), c.text(), "architecture-doc", "markdown", null));
        var targets = new ArrayList<TlrInput.Element>();
        String uri =
                store.put(c.project(), "x".getBytes(StandardCharsets.UTF_8)).uri();
        for (String path : paths) {
            String version = Ids.stableId("ver", c.project(), path);
            versions.add(
                    new TlrInput.Version(version, Ids.stableId("art", c.project(), path), path, "code", null, uri));
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
        return new TlrInput(
                c.project(),
                versions,
                elements(GOLDEN.resolve(c.project()).resolve(c.text() + ".json"), versions.get(0), "architecture-doc"),
                targets);
    }

    @Test
    void ardocodeEqualsStandaloneRunnerOnTheSameCodeModel() throws Exception {
        for (String[] spec : new String[][] {
            {"teastore", "teastore.txt", "teastore-code-model"},
            {"teastore", "teastore.txt", "teastore-code-model-noimage"},
            {"mediastore", "mediastore.txt", "mediastore-code-model"}
        }) {
            Path acm = CodeModels.acm(spec[2]);
            Case c = new Case(spec[0], spec[1], "-", "-", true);
            Map<String, String> paths = CodeModels.compilationUnits(acm);
            Set<String> standalone = standaloneArdocode(c, acm, paths);
            TlrInput in = ardocodeInput(c, paths.values());
            long lines =
                    in.sources().stream().filter(e -> e.sentenceIndex() != null).count();
            assertEquals(lines, standaloneSentences, spec[2] + ": the raw text splits like the rendering");
            int[] calls = {0};
            long start = System.nanoTime();
            var ctx = new TlrContext(
                    null,
                    store,
                    () -> false,
                    LoggerFactory.getLogger(ArdocodeApproach.class),
                    JsonMapper.shared().createObjectNode());
            List<CandidateLink> links =
                    new ArdocodeApproach(CorenlpClient.fromEnv(), fakePlugin(acm, calls)).recover(in, ctx);
            Map<String, Integer> sentence = new java.util.HashMap<>();
            in.sources().forEach(e -> sentence.put(e.id(), e.sentenceIndex() == null ? -1 : e.sentenceIndex() + 1));
            Map<String, String> file = new java.util.HashMap<>();
            in.targets().forEach(e -> file.put(e.id(), e.localKey()));
            Set<String> adapter = new TreeSet<>();
            links.forEach(l -> adapter.add(sentence.get(l.sourceId()) + " " + file.get(l.targetId())));
            System.out.printf(
                    "BENCHMARK ardocode %s: %d files, %d sentences, %d links, adapter %d ms%n",
                    spec[2], paths.size(), lines, adapter.size(), (System.nanoTime() - start) / 1_000_000);
            assertEquals(1, calls[0], "the ACM is fetched once per call");
            assertEquals(links.size(), adapter.size(), "one link per (sentence, file)");
            assertFalse(adapter.isEmpty(), spec[2]);
            assertEquals(standalone, adapter, spec[2]);
        }
    }

    @Test
    void twoRunsOnTheSameInputGiveTheSameLinks() throws Exception {
        Path acm = CodeModels.acm("mediastore-code-model");
        Case c = new Case("mediastore", "mediastore.txt", "-", "-", true);
        TlrInput in = ardocodeInput(c, CodeModels.compilationUnits(acm).values());
        var ctx = new TlrContext(
                null,
                store,
                () -> false,
                LoggerFactory.getLogger(ArdocodeApproach.class),
                JsonMapper.shared().createObjectNode());
        var first = new ArdocodeApproach(CorenlpClient.fromEnv(), fakePlugin(acm, new int[1])).recover(in, ctx);
        var second = new ArdocodeApproach(CorenlpClient.fromEnv(), fakePlugin(acm, new int[1])).recover(in, ctx);
        assertEquals(first, second);
        assertFalse(first.isEmpty());
    }

    /** ArDoCo's Text fills lazy caches while a run reads it: a run on a cached answer must equal a run on a fresh one. */
    @Test
    void twoRunsFromOneCacheEntryGiveTheLinksOfTwoRunsFromFreshAnnotations() throws Exception {
        Path acm = CodeModels.acm("mediastore-code-model");
        Case c = new Case("mediastore", "mediastore.txt", "pcm/ms.repository", "pcm-repository", true);
        TlrInput code = ardocodeInput(c, CodeModels.compilationUnits(acm).values());
        TlrInput model = input(c);
        var versions = new ArrayList<>(model.versions());
        versions.addAll(code.versions().subList(1, code.versions().size()));
        var targets = new ArrayList<>(model.targets());
        targets.addAll(code.targets());
        var both = new TlrInput(c.project(), versions, model.sources(), targets);
        int[] requests = {0};
        var counting =
                new CorenlpClient(
                        java.net.URI.create(CorenlpProvider.start()),
                        java.time.Duration.ofSeconds(300),
                        CorenlpClient.Eolonly.AUTO,
                        CorenlpClient.BACKOFF,
                        CorenlpClient.fromEnv().authorization()) {
                    @Override
                    public TextDto request(String text, boolean eolonly, java.util.function.BooleanSupplier cancelled) {
                        requests[0]++;
                        return super.request(text, eolonly, cancelled);
                    }
                };
        var ctx = new TlrContext(
                null,
                store,
                () -> false,
                LoggerFactory.getLogger(ArdocoApproach.class),
                JsonMapper.shared().createObjectNode());
        List<List<CandidateLink>> fresh = new ArrayList<>();
        for (int i = 0; i < 2; i++) {
            TextCache.clear();
            fresh.add(new ArdocoApproach(counting).recover(both, ctx));
            TextCache.clear();
            fresh.add(new ArdocodeApproach(counting, fakePlugin(acm, new int[1])).recover(both, ctx));
        }
        assertEquals(fresh.get(0), fresh.get(2));
        assertEquals(fresh.get(1), fresh.get(3));
        assertFalse(fresh.get(0).isEmpty());
        assertFalse(fresh.get(1).isEmpty());

        TextCache.clear();
        requests[0] = 0;
        List<List<CandidateLink>> cached = new ArrayList<>();
        for (int i = 0; i < 2; i++) {
            cached.add(new ArdocoApproach(counting).recover(both, ctx));
            cached.add(new ArdocodeApproach(counting, fakePlugin(acm, new int[1])).recover(both, ctx));
        }
        assertEquals(1, requests[0], "all four runs share one cache entry");
        assertEquals(fresh.subList(0, 2), cached.subList(0, 2));
        assertEquals(fresh.subList(0, 2), cached.subList(2, 4));
    }

    @Test
    void ardocoAndArdocodeAnnotateADocumentVersionOnce() throws Exception {
        TextCache.clear();
        Path acm = CodeModels.acm("mediastore-code-model");
        Case c = new Case("mediastore", "mediastore.txt", "pcm/ms.repository", "pcm-repository", true);
        Map<String, String> paths = CodeModels.compilationUnits(acm);
        TlrInput code = ardocodeInput(c, paths.values());
        TlrInput model = input(c);
        var versions = new ArrayList<>(model.versions());
        versions.addAll(code.versions().subList(1, code.versions().size()));
        var targets = new ArrayList<>(model.targets());
        targets.addAll(code.targets());
        var both = new TlrInput(c.project(), versions, model.sources(), targets);
        int[] requests = {0};
        var counting =
                new CorenlpClient(
                        java.net.URI.create(CorenlpProvider.start()),
                        java.time.Duration.ofSeconds(300),
                        CorenlpClient.Eolonly.AUTO,
                        CorenlpClient.BACKOFF,
                        CorenlpClient.fromEnv().authorization()) {
                    @Override
                    public TextDto request(String text, boolean eolonly, java.util.function.BooleanSupplier cancelled) {
                        requests[0]++;
                        return super.request(text, eolonly, cancelled);
                    }
                };
        var ctx = new TlrContext(
                null,
                store,
                () -> false,
                LoggerFactory.getLogger(ArdocoApproach.class),
                JsonMapper.shared().createObjectNode());
        assertFalse(new ArdocoApproach(counting).recover(both, ctx).isEmpty());
        assertFalse(new ArdocodeApproach(counting, fakePlugin(acm, new int[1]))
                .recover(both, ctx)
                .isEmpty());
        assertEquals(1, requests[0], "one annotation request for both approaches");
    }

    private long standaloneSentences;

    /** ardoco-sad-sam of sandman eval equal: header modelElementID,sentence; rows sorted by id, then sentence number. */
    private static void writeCsv(Case c, String side, Set<String> links) throws IOException {
        String dir = System.getProperty("SANDMAN_BENCHMARK_CSV_DIR", System.getenv("SANDMAN_BENCHMARK_CSV_DIR"));
        if (dir == null || dir.isBlank()) {
            return;
        }
        List<String[]> rows = new ArrayList<>();
        links.forEach(l -> rows.add(l.split(" ", 2)));
        rows.sort(java.util.Comparator.<String[], String>comparing(r -> r[1])
                .thenComparingInt(r -> Integer.parseInt(r[0])));
        StringBuilder csv = new StringBuilder("modelElementID,sentence\n");
        rows.forEach(r -> csv.append(r[1]).append(',').append(r[0]).append('\n'));
        String model = c.model().substring(c.model().lastIndexOf('/') + 1);
        Path out = Files.createDirectories(Path.of(dir)).resolve(c.project() + "-" + model + "." + side + ".csv");
        Files.writeString(out, csv.toString(), StandardCharsets.UTF_8);
    }

    /** Swattr on the raw text with the provider's annotation of the raw text (no eolonly), as (1-based sentence, id). */
    private Set<String> standalone(Case c) throws IOException {
        Path out = Files.createDirectories(
                tmp.resolve("standalone-" + c.project() + "-" + c.model().replace('/', '_')));
        Path text = FIXTURES.resolve(c.project()).resolve(c.text());
        var annotated =
                CorenlpClient.convert(CorenlpClient.fromEnv().request(Files.readString(text), false, () -> false));
        standaloneSentences = annotated.getSentences().size();
        var runner = new Swattr("bench");
        runner.setUp(
                text.toFile(),
                new ArchitectureConfiguration(
                        FIXTURES.resolve(c.project()).resolve(c.model()).toFile(), format(c.format())),
                SortedMaps.immutable.empty(),
                out.toFile());
        DataRepositoryHelper.putPreprocessingData(
                runner.getArdoco().getDataRepository(), new PreprocessingData(annotated));
        var result = new ArdocoResult(runner.runWithoutSaving());
        Set<String> links = new TreeSet<>();
        result.getArchitectureTraceLinks()
                .forEach(tl -> links.add((tl.getFirstEndpoint().getSentence().getSentenceNumber() + 1) + " "
                        + tl.getSecondEndpoint().getId()));
        return links;
    }

    private Set<String> adapter(TlrInput in) throws IOException {
        var approach = new ArdocoApproach();
        var ctx = new TlrContext(
                null,
                store,
                () -> false,
                LoggerFactory.getLogger(ArdocoApproach.class),
                JsonMapper.shared().createObjectNode());
        List<CandidateLink> links = approach.recover(in, ctx);
        Map<String, Integer> sentence = new java.util.HashMap<>();
        in.sources().forEach(e -> sentence.put(e.id(), e.sentenceIndex() == null ? -1 : e.sentenceIndex() + 1));
        Map<String, String> model = new java.util.HashMap<>();
        in.targets().forEach(e -> model.put(e.id(), e.localKey()));
        Set<String> set = new TreeSet<>();
        links.forEach(l -> set.add(sentence.get(l.sourceId()) + " " + model.get(l.targetId())));
        assertEquals(set.size(), links.size(), "one link per (sentence, model id)");
        return set;
    }

    private ArtifactStore store;

    /** The KG view of one benchmark text and model: elements from the extraction goldens, model file from the store. */
    private TlrInput input(Case c) throws IOException {
        store = new ArtifactStore(tmp.resolve("store"));
        String docVersion = Ids.stableId("ver", c.project(), "doc");
        String modelVersion = Ids.stableId("ver", c.project(), c.model());
        byte[] modelBytes = Files.readAllBytes(FIXTURES.resolve(c.project()).resolve(c.model()));
        var stored = store.put(c.project(), modelBytes);
        var versions = List.of(
                new TlrInput.Version(
                        docVersion,
                        Ids.stableId("art", c.project(), "doc"),
                        c.text(),
                        "architecture-doc",
                        "markdown",
                        null),
                new TlrInput.Version(
                        modelVersion,
                        Ids.stableId("art", c.project(), c.model()),
                        c.model(),
                        "architecture-model",
                        c.format(),
                        stored.uri()));
        return new TlrInput(
                c.project(),
                versions,
                elements(GOLDEN.resolve(c.project()).resolve(c.text() + ".json"), versions.get(0), "architecture-doc"),
                elements(
                        GOLDEN.resolve(c.project()).resolve(c.model() + ".json"),
                        versions.get(1),
                        "architecture-model"));
    }

    private static List<TlrInput.Element> elements(Path golden, TlrInput.Version v, String kind) throws IOException {
        JsonNode root = JsonMapper.shared().readTree(Files.readString(golden));
        List<TlrInput.Element> out = new ArrayList<>();
        for (JsonNode e : root.get("elements")) {
            String localKey = e.get("localKey").asString();
            out.add(new TlrInput.Element(
                    Ids.stableId("el", v.id(), localKey),
                    v.id(),
                    v.artifactId() + "#" + localKey,
                    localKey,
                    e.get("label").asString(),
                    e.get("kind").asString(),
                    e.get("name").asString(),
                    e.has("text") && !e.get("text").isNull() ? e.get("text").asString() : null,
                    e.has("sentenceIndex") && !e.get("sentenceIndex").isNull()
                            ? e.get("sentenceIndex").asInt()
                            : null,
                    kind));
        }
        return out;
    }

    private static ModelFormat format(String f) {
        return "uml".equals(f) ? ModelFormat.UML : ModelFormat.PCM;
    }
}
