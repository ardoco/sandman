package edu.kit.kastel.mcse.ardoco.sandman.inconsistency.ardoco;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;

import edu.kit.kastel.mcse.ardoco.core.api.models.ModelFormat;
import edu.kit.kastel.mcse.ardoco.sandman.ardocotext.CorenlpClient;
import edu.kit.kastel.mcse.ardoco.sandman.ardocotext.CorenlpProvider;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.io.TempDir;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;

/**
 * The detector's pipeline equals the standalone ArDoCo inconsistency runner on the raw benchmark text: both start from
 * the CoreNLP provider annotation. The run with ArDoCo's own links as external links (rule internal+similar) yields the
 * same findings as the run without the external step.
 */
class BenchmarkEquivalenceTest {

    private static final Path FIXTURES = Path.of("../../extraction-agent/tests/fixtures/benchmark");

    @TempDir
    Path tmp;

    @BeforeAll
    static void startProvider() throws Exception {
        CorenlpProvider.start();
    }

    @ParameterizedTest
    @CsvSource({
        "mediastore, mediastore.txt, pcm/ms.repository, PCM",
        "mediastore, mediastore.txt, uml/ms.uml, UML",
        "teastore, teastore.txt, pcm/teastore.repository, PCM",
        "teastore, teastore.txt, uml/teastore.uml, UML"
    })
    void pipelineEqualsStandaloneRunner(String project, String text, String model, ModelFormat format)
            throws Exception {
        Path raw = FIXTURES.resolve(project).resolve(text);
        Path modelFile = FIXTURES.resolve(project).resolve(model);
        var annotated =
                CorenlpClient.convert(CorenlpClient.fromEnv().request(Files.readString(raw), false, () -> false));

        var standalone = IdRunner.standalone(annotated, raw, modelFile, format, tmp);
        var expected = IdRunner.normalForm(IdRunner.inconsistencies(standalone));
        assertFalse(expected.modelIds().isEmpty() && expected.textFindings().isEmpty(), "no inconsistencies at all");

        var plain = IdRunner.normalForm(IdRunner.run(annotated, modelFile, format, null));
        assertEquals(expected, plain, "pipeline without external step");

        List<ExternalLink> links = IdRunner.selfLinks(standalone);
        assertFalse(links.isEmpty());
        var stats = new ExternalLinksStep.Stats();
        var linked = IdRunner.normalForm(IdRunner.run(annotated, modelFile, format, links, stats));
        assertEquals(expected, linked, "pipeline with ArDoCo's own links as external links");
        assertEquals(0, stats.unmapped);
        System.out.printf(
                "BENCHMARK %s/%s: %d model, %d text findings, %d links%n",
                project,
                model,
                expected.modelIds().size(),
                expected.textFindings().size(),
                links.size());
    }
}
