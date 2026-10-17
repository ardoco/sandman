package edu.kit.kastel.mcse.ardoco.sandman.inconsistency.ardoco;

import static org.junit.jupiter.api.Assertions.assertEquals;

import edu.kit.kastel.mcse.ardoco.sandman.ardocotext.CorenlpClient;
import edu.kit.kastel.mcse.ardoco.sandman.ardocotext.CorenlpProvider;
import edu.kit.kastel.mcse.ardoco.sandman.ardocotext.SentenceRendering;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Comparator;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

/**
 * The compose provider image (1.0.0) has no eolonly field, so the detector sends the lines separated by a blank line
 * (eolonly=false). Sentence i must still be line i on every benchmark document; annotateDto fails otherwise.
 */
class CorenlpInterimSplitTest {

    private static final Path GOLDEN = Path.of("../../extraction-agent/tests/golden/benchmark");

    @BeforeAll
    static void startProvider() throws Exception {
        CorenlpProvider.start();
    }

    @ParameterizedTest
    @ValueSource(strings = {"mediastore", "teastore", "teammates", "bigbluebutton", "jabref"})
    void blankLineSplitGivesOneSentencePerLine(String project) throws Exception {
        JsonNode root = JsonMapper.shared()
                .readTree(Files.readString(GOLDEN.resolve(project).resolve(project + ".txt.json")));
        var lines = new java.util.ArrayList<JsonNode>();
        root.get("elements").forEach(e -> {
            if ("sentence".equals(e.get("kind").asString())) {
                lines.add(e);
            }
        });
        lines.sort(Comparator.comparingInt(e -> e.get("sentenceIndex").asInt()));
        var rendered = lines.stream()
                .map(e -> SentenceRendering.line(e.get("text").asString()))
                .toList();
        var dto = CorenlpClient.fromEnv()
                .annotateDto(rendered, new CorenlpClient.Provider(false, "unknown"), () -> false);
        assertEquals(rendered.size(), dto.getSentences().size());
    }
}
