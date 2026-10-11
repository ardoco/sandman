package edu.kit.kastel.mcse.ardoco.sandman.tlr.ardoco;

import static org.junit.jupiter.api.Assertions.assertEquals;

import edu.kit.kastel.mcse.ardoco.core.textproviderjson.converter.ObjectToDtoConverter;
import edu.kit.kastel.mcse.ardoco.core.textproviderjson.dto.TextDto;
import edu.kit.kastel.mcse.ardoco.tlr.text.providers.informants.corenlp.textprocessor.TextProcessorLocal;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfSystemProperty;

/**
 * Manual check (not in CI): the service DTO equals the DTO of in-process CoreNLP 4.5.10 (ArDoCo's TextProcessorLocal)
 * for the five benchmark texts. Run with -Dcorenlp.parity=true. The service is the provider container, or the service
 * named by SANDMAN_CORENLP_URL and its token (for example the KIT instance through a local gateway).
 */
@EnabledIfSystemProperty(named = "corenlp.parity", matches = "true")
class CorenlpParityTest {

    private static final Path FIXTURES = Path.of("../../extraction-agent/tests/fixtures/benchmark");

    @Test
    void serviceAnnotationEqualsInProcessAnnotation() throws Exception {
        if (System.getenv("SANDMAN_CORENLP_URL") == null) {
            CorenlpProvider.start();
        }
        var client = CorenlpClient.fromEnv();
        var converter = new ObjectToDtoConverter();
        var differences = new java.util.ArrayList<String>();
        for (String project : List.of("mediastore", "teastore", "teammates", "bigbluebutton", "jabref")) {
            String text = Files.readString(FIXTURES.resolve(project).resolve(project + ".txt"));
            TextDto service = client.request(text, false, () -> false);
            TextDto local = converter.convertTextToDTO(new TextProcessorLocal().processText(text));
            if (service.equals(local)) {
                System.out.println("PARITY " + project + ": equal, "
                        + service.getSentences().size() + " sentences");
                continue;
            }
            int n = Math.min(service.getSentences().size(), local.getSentences().size());
            String first = service.getSentences().size() + " vs "
                    + local.getSentences().size() + " sentences";
            for (int i = 0; i < n; i++) {
                if (!service.getSentences().get(i).equals(local.getSentences().get(i))) {
                    first = "first difference in sentence " + (i + 1) + ": "
                            + service.getSentences().get(i).getText();
                    break;
                }
            }
            System.out.println("PARITY " + project + ": DIFFERENT, " + first);
            differences.add(project);
        }
        assertEquals(List.of(), differences);
    }
}
