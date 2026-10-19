package edu.kit.kastel.mcse.ardoco.sandman.inconsistency.ardoco;

import edu.kit.kastel.mcse.ardoco.core.api.models.ModelFormat;
import edu.kit.kastel.mcse.ardoco.sandman.ardocotext.CorenlpClient;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Set;

/** Public entry to the standalone ArDoCo runner for tests in other modules (Quarkus classpath, T3.3). */
public final class StandaloneBaseline {

    /** Model ids and "sentence word" pairs (1-based sentence) as the standalone runner reports them. */
    public record Findings(Set<String> modelIds, Set<String> textFindings) {}

    private StandaloneBaseline() {}

    /** CoreNLP from the environment (CorenlpProvider.start()), raw text annotated without eolonly. */
    public static Findings run(Path rawText, Path model, ModelFormat format, Path tmp) throws IOException {
        var annotated =
                CorenlpClient.convert(CorenlpClient.fromEnv().request(Files.readString(rawText), false, () -> false));
        var form = IdRunner.normalForm(
                IdRunner.inconsistencies(IdRunner.standalone(annotated, rawText, model, format, tmp)));
        return new Findings(form.modelIds(), form.textFindings());
    }
}
