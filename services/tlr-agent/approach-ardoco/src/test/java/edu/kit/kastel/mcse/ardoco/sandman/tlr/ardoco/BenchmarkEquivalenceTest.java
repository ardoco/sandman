package edu.kit.kastel.mcse.ardoco.sandman.tlr.ardoco;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import edu.kit.kastel.mcse.ardoco.core.api.models.ModelFormat;
import edu.kit.kastel.mcse.ardoco.sandman.agent.ArtifactStore;
import edu.kit.kastel.mcse.ardoco.sandman.agent.Ids;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.CandidateLink;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.TlrContext;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.TlrInput;
import edu.kit.kastel.mcse.ardoco.tlr.execution.Swattr;
import edu.kit.kastel.mcse.ardoco.tlr.models.agents.ArchitectureConfiguration;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeSet;
import org.eclipse.collections.api.factory.SortedMaps;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.slf4j.LoggerFactory;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

/**
 * M2 exit: the adapter (KG rendering, injected sentence split) yields the link set of the standalone ArDoCo runner on
 * the raw benchmark text, as (1-based sentence, model id) pairs. Projects whose raw text CoreNLP splits into more
 * sentences than lines are reported as excluded, except the required cases.
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
            assertEquals(standalone, adapter, c.name());
            if (c.required()) {
                assertTrue(!adapter.isEmpty(), c.name());
            }
        }
        excluded.forEach(e -> System.out.println("BENCHMARK EXCLUDED " + e));
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
    }

    private long standaloneSentences;

    /** Swattr on the raw text file, links parsed from sadSamTlr_<name>.csv ("modelElementID,sentence", 1-based). */
    private Set<String> standalone(Case c) throws IOException {
        Path out = Files.createDirectories(
                tmp.resolve("standalone-" + c.project() + "-" + c.model().replace('/', '_')));
        var runner = new Swattr("bench");
        runner.setUp(
                FIXTURES.resolve(c.project()).resolve(c.text()).toFile(),
                new ArchitectureConfiguration(
                        FIXTURES.resolve(c.project()).resolve(c.model()).toFile(), format(c.format())),
                SortedMaps.immutable.empty(),
                out.toFile());
        var result = runner.run();
        standaloneSentences = result.getText().getSentences().size();
        Path csv = out.resolve("sadSamTlr_bench.csv");
        Set<String> links = new TreeSet<>();
        if (Files.exists(csv)) {
            Files.readAllLines(csv).stream().skip(1).filter(l -> !l.isBlank()).forEach(l -> {
                String[] p = l.split(",");
                links.add(p[1].trim() + " " + p[0].trim());
            });
        }
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
