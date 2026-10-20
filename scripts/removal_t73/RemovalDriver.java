import edu.kit.kastel.mcse.ardoco.core.api.PreprocessingData;
import edu.kit.kastel.mcse.ardoco.core.api.models.ModelFormat;
import edu.kit.kastel.mcse.ardoco.core.api.models.code.CodeCompilationUnit;
import edu.kit.kastel.mcse.ardoco.core.api.output.ArdocoResult;
import edu.kit.kastel.mcse.ardoco.core.common.util.DataRepositoryHelper;
import edu.kit.kastel.mcse.ardoco.sandman.ardocotext.CorenlpClient;
import edu.kit.kastel.mcse.ardoco.sandman.ardocotext.CorenlpProvider;
import edu.kit.kastel.mcse.ardoco.tlr.execution.Transarc;
import edu.kit.kastel.mcse.ardoco.tlr.models.agents.ArchitectureConfiguration;
import edu.kit.kastel.mcse.ardoco.tlr.models.agents.CodeConfiguration;
import java.nio.file.*;
import java.util.*;
import java.util.stream.*;
import org.eclipse.collections.api.factory.SortedMaps;

/**
 * TLR command of `sandman eval removal` (T7.3). Argument: jobs directory. Each job directory holds text.txt, code.acm and
 * model.repository (PCM). The driver runs transarc and writes links.csv (ae_id,ce_ids) with the SAM-code links.
 * Run from services/tlr-agent/approach-ardoco with the test classpath of that module (see run.sh).
 */
public class RemovalDriver {
    public static void main(String[] a) throws Exception {
        Path jobs = Path.of(a[0]);
        CorenlpProvider.start();
        Map<String, edu.kit.kastel.mcse.ardoco.core.api.text.Text> annotated = new HashMap<>();
        List<Path> dirs;
        try (Stream<Path> s = Files.list(jobs)) {
            dirs = s.filter(Files::isDirectory).sorted().collect(Collectors.toList());
        }
        for (Path job : dirs) {
            Path text = job.resolve("text.txt");
            String content = Files.readString(text);
            var doc = annotated.computeIfAbsent(content, c -> {
                try {
                    return CorenlpClient.convert(CorenlpClient.fromEnv().request(c, false, () -> false));
                } catch (Exception e) {
                    throw new IllegalStateException(e);
                }
            });
            var runner = new Transarc(job.getFileName().toString());
            runner.setUp(
                    text.toFile(),
                    new ArchitectureConfiguration(job.resolve("model.repository").toFile(), ModelFormat.PCM),
                    new CodeConfiguration(job.resolve("code.acm").toFile(), CodeConfiguration.CodeConfigurationType.ACM_FILE),
                    SortedMaps.immutable.empty(),
                    Files.createDirectories(job.resolve("work")).toFile());
            DataRepositoryHelper.putPreprocessingData(runner.getArdoco().getDataRepository(), new PreprocessingData(doc));
            var result = new ArdocoResult(runner.runWithoutSaving());
            var rows = new TreeSet<String>();
            result.getSamCodeTraceLinks().forEach(t -> rows.add(t.getFirstEndpoint().getId() + ","
                    + ((CodeCompilationUnit) t.getSecondEndpoint()).getPath()));
            Files.writeString(job.resolve("links.csv"), "ae_id,ce_ids\n" + String.join("\n", rows) + "\n");
            System.out.println(job.getFileName() + ": " + rows.size() + " links");
        }
        System.exit(0);
    }
}
