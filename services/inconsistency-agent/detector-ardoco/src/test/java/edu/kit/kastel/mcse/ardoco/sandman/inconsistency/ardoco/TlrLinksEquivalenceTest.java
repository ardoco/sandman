package edu.kit.kastel.mcse.ardoco.sandman.inconsistency.ardoco;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import edu.kit.kastel.mcse.ardoco.core.api.models.ModelFormat;
import edu.kit.kastel.mcse.ardoco.core.api.text.Text;
import edu.kit.kastel.mcse.ardoco.sandman.ardocotext.CorenlpClient;
import edu.kit.kastel.mcse.ardoco.sandman.ardocotext.CorenlpProvider;
import edu.kit.kastel.mcse.ardoco.sandman.ardocotext.SentenceRendering;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.TreeSet;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.io.TempDir;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

/**
 * T3.3: the links of the TLR approach 'ardoco' (recorded by approach-ardoco's BenchmarkEquivalenceTest into
 * src/test/resources/tlr-ardoco-links, 1-based sentence and model id) fed as external links give the findings of the
 * standalone ArDoCo runner. The text is rendered from the extraction goldens like the detector does; the standalone
 * runner reads the raw benchmark text. Model findings must be equal. Text findings are equal too, except on MediaStore:
 * the TLR release links 18 sentence pairs where the ID branch's own TLR finds 29, so two words ("download" in sentences
 * 9 and 37) stay reported (open point of T3.3, see the task document). The alignment rate is the share of lines that ArDoCo sentence splitting of the raw
 * text reproduces (ADR-0015 S3).
 */
class TlrLinksEquivalenceTest {

    private static final Path FIXTURES = Path.of("../../extraction-agent/tests/fixtures/benchmark");
    private static final Path GOLDEN = Path.of("../../extraction-agent/tests/golden/benchmark");
    private static final Path LINKS = Path.of("src/test/resources/tlr-ardoco-links");

    @TempDir
    Path tmp;

    @BeforeAll
    static void startProvider() throws Exception {
        CorenlpProvider.start();
    }

    @ParameterizedTest
    @CsvSource({
        "mediastore, mediastore.txt, pcm/ms.repository, PCM, ms.repository, 9 download|37 download",
        "mediastore, mediastore.txt, uml/ms.uml, UML, ms.uml, 9 download|37 download",
        "teastore, teastore.txt, pcm/teastore.repository, PCM, teastore.repository, -",
        "teastore, teastore.txt, uml/teastore.uml, UML, teastore.uml, -",
        "teammates, teammates.txt, pcm/teammates.repository, PCM, teammates.repository, -",
        "bigbluebutton, bigbluebutton.txt, pcm/bbb.repository, PCM, bbb.repository, -",
        "jabref, jabref.txt, pcm/jabref.repository, PCM, jabref.repository, -"
    })
    void tlrLinksGiveTheStandaloneFindings(
            String project, String text, String model, ModelFormat format, String name, String extra) throws Exception {
        Path raw = FIXTURES.resolve(project).resolve(text);
        Path modelFile = FIXTURES.resolve(project).resolve(model);
        List<String> lines = lines(GOLDEN.resolve(project).resolve(text + ".json"));
        var corenlp = CorenlpClient.fromEnv();
        Text rawAnnotated = CorenlpClient.convert(corenlp.request(Files.readString(raw), false, () -> false));
        Text rendered = CorenlpClient.convert(corenlp.request(SentenceRendering.text(lines), true, () -> false));

        int aligned = 0;
        for (int i = 0; i < Math.min(lines.size(), rawAnnotated.getSentences().size()); i++) {
            aligned += SentenceRendering.normalize(
                                    rawAnnotated.getSentences().get(i).getText())
                            .equals(SentenceRendering.normalize(lines.get(i)))
                    ? 1
                    : 0;
        }
        assertEquals(lines.size(), rendered.getSentences().size(), "eolonly: one sentence per line");

        var standalone = IdRunner.standalone(rawAnnotated, raw, modelFile, format, tmp);
        var expected = IdRunner.normalForm(IdRunner.inconsistencies(standalone));
        assertFalse(expected.modelIds().isEmpty() && expected.textFindings().isEmpty(), "no inconsistencies at all");

        List<ExternalLink> tlr = readLinks(LINKS.resolve(project + "-" + name + ".csv"));
        assertFalse(tlr.isEmpty());
        var stats = new ExternalLinksStep.Stats();
        var actual = IdRunner.normalForm(IdRunner.run(rendered, modelFile, format, tlr, stats));
        assertEquals(0, stats.unmapped, "every TLR link maps");

        var own = new TreeSet<>(Comparator.comparing(ExternalLink::sentence).thenComparing(ExternalLink::modelId));
        own.addAll(IdRunner.selfLinks(standalone));
        var fed = new TreeSet<>(own.comparator());
        fed.addAll(tlr);
        System.out.printf(
                "T33 %s/%s: lines %d, alignment %d/%d (%.1f %%), raw split %d, tlr links %d (ID-branch own links %d, equal %b), "
                        + "findings %d model + %d text, input %d, mapped %d, unmapped %d%n",
                project,
                model,
                lines.size(),
                aligned,
                lines.size(),
                100.0 * aligned / lines.size(),
                rawAnnotated.getSentences().size(),
                tlr.size(),
                own.size(),
                own.equals(fed),
                expected.modelIds().size(),
                expected.textFindings().size(),
                stats.input,
                stats.mapped,
                stats.unmapped);
        assertEquals(expected.modelIds(), actual.modelIds(), "model findings");
        var extraText = new TreeSet<>(actual.textFindings());
        extraText.removeAll(expected.textFindings());
        assertEquals(
                "-".equals(extra) ? new TreeSet<String>() : new TreeSet<>(List.of(extra.split("\\|"))),
                extraText,
                "text findings beyond the standalone runner");
        assertTrue(actual.textFindings().containsAll(expected.textFindings()), "no standalone text finding is lost");
    }

    @ParameterizedTest
    @CsvSource({"pcm/ms.repository, PCM, ms.repository", "uml/ms.uml, UML, ms.uml"})
    void mediaStoreRawCounts(String model, ModelFormat format, String name) throws Exception {
        Path raw = FIXTURES.resolve("mediastore/mediastore.txt");
        Path modelFile = FIXTURES.resolve("mediastore").resolve(model);
        List<String> lines = lines(GOLDEN.resolve("mediastore/mediastore.txt.json"));
        var corenlp = CorenlpClient.fromEnv();
        Text rawAnnotated = CorenlpClient.convert(corenlp.request(Files.readString(raw), false, () -> false));
        Text rendered = CorenlpClient.convert(corenlp.request(SentenceRendering.text(lines), true, () -> false));
        var standalone = IdRunner.standalone(rawAnnotated, raw, modelFile, format, tmp);
        var own = IdRunner.extract(IdRunner.inconsistencies(standalone));
        var sentences17 = List.of(1, 9, 12, 13, 20, 22, 23, 24, 25, 27, 28, 29, 31, 32, 33, 34, 35);
        assertEquals(6, own.models().size());
        assertEquals(36, own.words().size());
        assertEquals(sentences17, sentences(own));

        var selfLinks = IdRunner.extract(IdRunner.run(rendered, modelFile, format, IdRunner.selfLinks(standalone)));
        assertEquals(6, selfLinks.models().size());
        assertEquals(36, selfLinks.words().size());
        assertEquals(sentences17, sentences(selfLinks));

        var tlr = IdRunner.extract(
                IdRunner.run(rendered, modelFile, format, readLinks(LINKS.resolve("mediastore-" + name + ".csv"))));
        assertEquals(6, tlr.models().size());
        assertEquals(38, tlr.words().size(), "T3.3 deviation: release TLR links fewer pairs than the ID branch");
        var expectedTlr = new ArrayList<>(sentences17);
        expectedTlr.add(37);
        assertEquals(expectedTlr, sentences(tlr));
    }

    private static List<Integer> sentences(Mapping.Raw raw) {
        return new TreeSet<>(raw.words().stream().map(Mapping.WordHit::sentence).toList())
                .stream().toList();
    }

    private static List<String> lines(Path golden) throws IOException {
        JsonNode root = JsonMapper.shared().readTree(Files.readString(golden));
        var sentences = new ArrayList<JsonNode>();
        for (JsonNode e : root.get("elements")) {
            if ("sentence".equals(e.get("kind").asString())) {
                sentences.add(e);
            }
        }
        sentences.sort(Comparator.comparingInt(e -> e.get("sentenceIndex").asInt()));
        return sentences.stream()
                .map(e -> SentenceRendering.line(e.get("text").asString()))
                .toList();
    }

    private static List<ExternalLink> readLinks(Path csv) throws IOException {
        List<ExternalLink> out = new ArrayList<>();
        for (String row : Files.readAllLines(csv).stream().skip(1).toList()) {
            String[] p = row.split(",");
            out.add(new ExternalLink(Integer.parseInt(p[1]) - 1, p[0]));
        }
        return out;
    }
}
