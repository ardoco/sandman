package edu.kit.kastel.mcse.ardoco.sandman.tlr.ardoco;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import edu.kit.kastel.mcse.ardoco.core.api.PreprocessingData;
import edu.kit.kastel.mcse.ardoco.core.api.models.ModelFormat;
import edu.kit.kastel.mcse.ardoco.core.api.models.code.CodeCompilationUnit;
import edu.kit.kastel.mcse.ardoco.core.api.output.ArdocoResult;
import edu.kit.kastel.mcse.ardoco.core.common.util.DataRepositoryHelper;
import edu.kit.kastel.mcse.ardoco.sandman.agent.ArtifactStore;
import edu.kit.kastel.mcse.ardoco.sandman.agent.Ids;
import edu.kit.kastel.mcse.ardoco.sandman.ardocotext.CorenlpClient;
import edu.kit.kastel.mcse.ardoco.sandman.ardocotext.CorenlpProvider;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.CandidateLink;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.TlrContext;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.TlrInput;
import edu.kit.kastel.mcse.ardoco.tlr.execution.Transarc;
import edu.kit.kastel.mcse.ardoco.tlr.models.agents.ArchitectureConfiguration;
import edu.kit.kastel.mcse.ardoco.tlr.models.agents.CodeConfiguration;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeSet;
import org.eclipse.collections.api.factory.SortedMaps;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.slf4j.LoggerFactory;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

/**
 * T2.7: the Transarc adapter yields the SAD-code and SAM-code links of the standalone ArDoCo Transarc runner on the raw
 * benchmark text, the benchmark model and the ACM of code-extractor-ardoco. Projects without an ACM in
 * ~/.cache/sandman/benchmark-code, and projects whose raw text CoreNLP splits into more sentences than lines, are
 * reported as excluded, except the required cases.
 */
class TransarcEquivalenceTest {

    private static final Path FIXTURES = Path.of("../../extraction-agent/tests/fixtures/benchmark");
    private static final Path GOLDEN = Path.of("../../extraction-agent/tests/golden/benchmark");

    private record Case(String project, String model, String format, boolean required) {
        String text() {
            return project + ".txt";
        }
    }

    private static final List<Case> CASES = List.of(
            new Case("mediastore", "pcm/ms.repository", "pcm-repository", true),
            new Case("teastore", "pcm/teastore.repository", "pcm-repository", true),
            new Case("teammates", "pcm/teammates.repository", "pcm-repository", false),
            new Case("bigbluebutton", "pcm/bbb.repository", "pcm-repository", false),
            new Case("jabref", "pcm/jabref.repository", "pcm-repository", false));

    @TempDir
    Path tmp;

    @BeforeAll
    static void startProvider() throws Exception {
        CorenlpProvider.start();
    }

    @Test
    void adapterEqualsStandaloneTransarc() throws Exception {
        List<String> excluded = new ArrayList<>();
        for (Case c : CASES) {
            Path acm = Path.of(
                    System.getProperty("user.home"),
                    ".cache",
                    "sandman",
                    "benchmark-code",
                    c.project() + "-code-model.acm");
            if (!Files.isRegularFile(acm)) {
                assertFalse(c.required(), "required case without code model " + acm);
                excluded.add(c.project() + ": no code model " + acm);
                continue;
            }
            Map<String, String> paths = CodeModels.compilationUnits(acm);
            var raw = CorenlpClient.convert(CorenlpClient.fromEnv()
                    .request(Files.readString(FIXTURES.resolve(c.project()).resolve(c.text())), false, () -> false));
            TlrInput in = input(c, paths.values());
            long lines =
                    in.sources().stream().filter(e -> e.sentenceIndex() != null).count();
            if (raw.getSentences().size() != lines) {
                String msg = c.project() + ": raw text splits into "
                        + raw.getSentences().size() + " sentences, rendering has " + lines + " lines";
                assertFalse(c.required(), "required case excluded: " + msg);
                excluded.add(msg);
                continue;
            }
            long start = System.nanoTime();
            Set<String>[] standalone = standalone(c, acm, raw);
            long standaloneMs = (System.nanoTime() - start) / 1_000_000;
            start = System.nanoTime();
            var ctx = new TlrContext(
                    null,
                    store,
                    () -> false,
                    LoggerFactory.getLogger(TransarcApproach.class),
                    JsonMapper.shared().createObjectNode());
            TextCache.clear();
            ArdocodeApproach.clearCache();
            TransarcApproach.clearCache();
            List<CandidateLink> links = new TransarcApproach(
                            CorenlpClient.fromEnv(), BenchmarkEquivalenceTest.fakePlugin(acm, new int[1]))
                    .recover(in, ctx);
            long adapterMs = (System.nanoTime() - start) / 1_000_000;
            Set<String>[] adapter = adapter(in, links);
            System.out.printf(
                    "BENCHMARK transarc %s: %d files, %d sentences, %d SAD-code, %d SAM-code, standalone %d ms, adapter %d ms%n",
                    c.project(), paths.size(), lines, adapter[0].size(), adapter[1].size(), standaloneMs, adapterMs);
            assertEquals(standalone[0], adapter[0], c.project() + " SAD-code");
            assertEquals(standalone[1], adapter[1], c.project() + " SAM-code");
            Set<String>[] reused = adapter(
                    in,
                    new TransarcApproach(CorenlpClient.fromEnv(), BenchmarkEquivalenceTest.fakePlugin(acm, new int[1]))
                            .recover(in, ctx));
            assertEquals(standalone[0], reused[0], c.project() + " SAD-code, SAM-code links reused");
            assertEquals(standalone[1], reused[1], c.project() + " SAM-code, links reused");
            if (c.required()) {
                assertFalse(adapter[0].isEmpty(), c.project() + " SAD-code");
                assertFalse(adapter[1].isEmpty(), c.project() + " SAM-code");
            }
        }
        excluded.forEach(e -> System.out.println("BENCHMARK EXCLUDED transarc " + e));
        assertTrue(excluded.size() < CASES.size());
    }

    private Set<String>[] standalone(Case c, Path acm, edu.kit.kastel.mcse.ardoco.core.api.text.Text annotated)
            throws IOException {
        Path out = Files.createDirectories(tmp.resolve("standalone-" + c.project()));
        var runner = new Transarc("bench");
        runner.setUp(
                FIXTURES.resolve(c.project()).resolve(c.text()).toFile(),
                new ArchitectureConfiguration(
                        FIXTURES.resolve(c.project()).resolve(c.model()).toFile(), ModelFormat.PCM),
                new CodeConfiguration(acm.toFile(), CodeConfiguration.CodeConfigurationType.ACM_FILE),
                SortedMaps.immutable.empty(),
                out.toFile());
        DataRepositoryHelper.putPreprocessingData(
                runner.getArdoco().getDataRepository(), new PreprocessingData(annotated));
        var result = new ArdocoResult(runner.runWithoutSaving());
        Set<String> sadCode = new TreeSet<>();
        result.getSadCodeTraceLinks()
                .forEach(tl -> sadCode.add(tl.getFirstEndpoint().getSentence().getSentenceNumber() + " "
                        + ArdocodeApproach.pathOf((CodeCompilationUnit) tl.getSecondEndpoint())));
        Set<String> samCode = new TreeSet<>();
        result.getSamCodeTraceLinks()
                .forEach(tl -> samCode.add(tl.getFirstEndpoint().getId() + " "
                        + ArdocodeApproach.pathOf((CodeCompilationUnit) tl.getSecondEndpoint())));
        @SuppressWarnings("unchecked")
        Set<String>[] both = new Set[] {sadCode, samCode};
        return both;
    }

    private static Set<String>[] adapter(TlrInput in, List<CandidateLink> links) {
        Map<String, Integer> sentence = new HashMap<>();
        Map<String, String> key = new HashMap<>();
        in.sources().forEach(e -> {
            sentence.put(e.id(), e.sentenceIndex() == null ? -1 : e.sentenceIndex());
            key.put(e.id(), e.localKey());
        });
        in.targets().forEach(e -> key.put(e.id(), e.localKey()));
        Set<String> sadCode = new TreeSet<>();
        Set<String> samCode = new TreeSet<>();
        for (CandidateLink l : links) {
            if ("documents".equals(l.linkType())) {
                sadCode.add(sentence.get(l.sourceId()) + " " + key.get(l.targetId()));
            } else {
                assertEquals("implements", l.linkType());
                samCode.add(key.get(l.sourceId()) + " " + key.get(l.targetId()));
            }
        }
        assertEquals(sadCode.size() + samCode.size(), links.size(), "one link per distinct pair");
        @SuppressWarnings("unchecked")
        Set<String>[] both = new Set[] {sadCode, samCode};
        return both;
    }

    private ArtifactStore store;

    private TlrInput input(Case c, java.util.Collection<String> paths) throws IOException {
        store = new ArtifactStore(tmp.resolve("store-" + c.project()));
        String docVersion = Ids.stableId("ver", c.project(), "doc");
        String modelVersion = Ids.stableId("ver", c.project(), c.model());
        var stored = store.put(
                c.project(), Files.readAllBytes(FIXTURES.resolve(c.project()).resolve(c.model())));
        var versions = new ArrayList<TlrInput.Version>();
        versions.add(new TlrInput.Version(
                docVersion, Ids.stableId("art", c.project(), "doc"), c.text(), "architecture-doc", "markdown", null));
        versions.add(new TlrInput.Version(
                modelVersion,
                Ids.stableId("art", c.project(), c.model()),
                c.model(),
                "architecture-model",
                c.format(),
                stored.uri()));
        String uri =
                store.put(c.project(), "x".getBytes(StandardCharsets.UTF_8)).uri();
        var targets = new ArrayList<TlrInput.Element>();
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
        var sources = new ArrayList<>(
                elements(GOLDEN.resolve(c.project()).resolve(c.text() + ".json"), versions.get(0), "architecture-doc"));
        sources.addAll(elements(
                GOLDEN.resolve(c.project()).resolve(c.model() + ".json"), versions.get(1), "architecture-model"));
        return new TlrInput(c.project(), versions, sources, targets);
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
}
