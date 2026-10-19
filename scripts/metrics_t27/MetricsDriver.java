import edu.kit.kastel.mcse.ardoco.core.api.PreprocessingData;
import edu.kit.kastel.mcse.ardoco.core.api.models.ModelFormat;
import edu.kit.kastel.mcse.ardoco.core.api.models.code.CodeCompilationUnit;
import edu.kit.kastel.mcse.ardoco.core.api.output.ArdocoResult;
import edu.kit.kastel.mcse.ardoco.core.common.util.DataRepositoryHelper;
import edu.kit.kastel.mcse.ardoco.sandman.ardocotext.CorenlpClient;
import edu.kit.kastel.mcse.ardoco.sandman.ardocotext.CorenlpProvider;
import edu.kit.kastel.mcse.ardoco.tlr.execution.Ardocode;
import edu.kit.kastel.mcse.ardoco.tlr.execution.Transarc;
import edu.kit.kastel.mcse.ardoco.tlr.models.agents.ArchitectureConfiguration;
import edu.kit.kastel.mcse.ardoco.tlr.models.agents.CodeConfiguration;
import java.nio.file.*;
import java.util.*;
import org.eclipse.collections.api.factory.SortedMaps;

/**
 * Throw-away driver of scripts/metrics_t27.py: writes the SAD-code links of ardocode and transarc as CSV
 * (sentenceID,codeID, 1-based sentence, undecoded ACM path). Arguments: fixtures-dir acm-dir listings-dir out-dir
 * project... Run from services/tlr-agent/approach-ardoco with the test classpath of that module.
 */
public class MetricsDriver {
    static final Map<String, String> PCM = Map.of(
            "mediastore", "ms.repository", "teastore", "teastore.repository", "teammates", "teammates.repository",
            "bigbluebutton", "bbb.repository", "jabref", "jabref.repository");

    public static void main(String[] a) throws Exception {
        Path fixtures = Path.of(a[0]), acms = Path.of(a[1]), listings = Path.of(a[2]), out = Files.createDirectories(Path.of(a[3]));
        CorenlpProvider.start();
        for (int i = 4; i < a.length; i++) {
            String p = a[i];
            Path text = fixtures.resolve(p).resolve(p + ".txt");
            var pre = new PreprocessingData(CorenlpClient.convert(CorenlpClient.fromEnv().request(Files.readString(text), false, () -> false)));
            var code = new CodeConfiguration(acms.resolve(p + "-code-model.acm").toFile(), CodeConfiguration.CodeConfigurationType.ACM_FILE);
            Path work = Files.createDirectories(out.resolve("work-" + p));

            var ardocode = new Ardocode(p);
            ardocode.setUp(text.toFile(), code, SortedMaps.immutable.empty(), Files.createDirectories(work.resolve("ardocode")).toFile());
            DataRepositoryHelper.putPreprocessingData(ardocode.getArdoco().getDataRepository(), pre);
            write(out.resolve(p + ".ardocode.csv"), new ArdocoResult(ardocode.runWithoutSaving()));

            write(out.resolve(p + ".transarc-gold.csv"), transarc(p, text, code, pre, work.resolve("gold"),
                    new ArchitectureConfiguration(fixtures.resolve(p).resolve("pcm").resolve(PCM.get(p)).toFile(), ModelFormat.PCM)));
            write(out.resolve(p + ".transarc-derived.csv"), transarc(p, text, code, pre, work.resolve("derived"),
                    new ArchitectureConfiguration(listings.resolve(p + ".components").toFile(), ModelFormat.COMPONENT_LISTING)));
        }
        System.exit(0);
    }

    static ArdocoResult transarc(String p, Path text, CodeConfiguration code, PreprocessingData pre, Path work, ArchitectureConfiguration arch) throws Exception {
        var runner = new Transarc(p);
        runner.setUp(text.toFile(), arch, code, SortedMaps.immutable.empty(), Files.createDirectories(work).toFile());
        DataRepositoryHelper.putPreprocessingData(runner.getArdoco().getDataRepository(), pre);
        return new ArdocoResult(runner.runWithoutSaving());
    }

    static void write(Path file, ArdocoResult r) throws Exception {
        var rows = new TreeSet<String>();
        r.getSadCodeTraceLinks().forEach(t -> rows.add((t.getFirstEndpoint().getSentence().getSentenceNumber() + 1) + ","
                + ((CodeCompilationUnit) t.getSecondEndpoint()).getPath()));
        Files.writeString(file, "sentenceID,codeID\n" + String.join("\n", rows) + "\n");
        System.out.println(file.getFileName() + ": " + rows.size() + " links");
    }
}
