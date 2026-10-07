package edu.kit.kastel.mcse.ardoco.sandman.spike;

import java.io.File;
import java.nio.file.Files;
import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;

import org.eclipse.collections.api.factory.SortedMaps;

import edu.kit.kastel.mcse.ardoco.core.api.models.ModelFormat;
import edu.kit.kastel.mcse.ardoco.core.api.output.ArdocoResult;
import edu.kit.kastel.mcse.ardoco.id.execution.runner.InconsistencyDetection;
import edu.kit.kastel.sdq.lissa.ratlr.configuration.ModuleConfiguration;
import edu.kit.kastel.sdq.lissa.ratlr.context.ContextStore;
import edu.kit.kastel.sdq.lissa.ratlr.knowledge.Artifact;
import edu.kit.kastel.sdq.lissa.ratlr.preprocessor.ModelUMLPreprocessor;

/** Criterion (b): ArDoCo InconsistencyDetection on MediaStore (spike T0.12: 6 + 36 = 42) and LiSSA's UML preprocessor, inside Quarkus. */
public final class ArdocoProbe {

    static final File BENCH = new File(System.getProperty("ardoco.benchmark",
            "/private/tmp/claude-501/ardoco-spike/ardoco/core/tests-base/src/main/resources/benchmark/mediastore"));

    public static int run() throws Exception {
        var out = Files.createTempDirectory("ardoco-probe").toFile();
        var runner = new InconsistencyDetection("probe");
        runner.setUp(new File(BENCH, "text_2016/mediastore.txt"), new File(BENCH, "model_2016/pcm/ms.repository"), ModelFormat.PCM,
                SortedMaps.immutable.empty(), out);
        ArdocoResult result = new ArdocoResult(runner.runWithoutSaving());
        var all = result.getAllInconsistencies();
        Map<String, Long> byType = all.stream().collect(Collectors.groupingBy(i -> i.getClass().getSimpleName(), Collectors.counting()));
        System.out.println("ARDOCO findings=" + all.size() + " byType=" + byType);

        String uml = Files.readString(new File(BENCH, "model_2016/uml/ms.uml").toPath());
        var elements = new ModelUMLPreprocessor(new ModuleConfiguration("model_uml", Map.of()), new ContextStore())
                .preprocess(List.of(new Artifact("ms.uml", Artifact.ArtifactType.SOFTWARE_ARCHITECTURE_MODEL, uml)));
        System.out.println("LISSA ModelUMLPreprocessor elements=" + elements.size());
        return all.size();
    }
}
