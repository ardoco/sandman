package edu.kit.kastel.mcse.ardoco.sandman.code;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import edu.kit.kastel.mcse.ardoco.core.api.models.Metamodel;
import edu.kit.kastel.mcse.ardoco.core.api.models.code.CodeItemRepository;
import edu.kit.kastel.mcse.ardoco.core.common.IdentifierProvider;
import edu.kit.kastel.mcse.ardoco.tlr.models.connectors.generators.code.AllLanguagesExtractor;
import java.nio.file.Files;
import java.nio.file.Path;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

class CodeModelSkillTest {

    static ObjectNode codeModelInput(Path store) {
        ObjectNode input = JsonMapper.shared().createObjectNode();
        input.put("projectId", "mini");
        var current = input.putArray("currentArtifacts");
        for (String path : Fixtures.files(store)) {
            current.addObject()
                    .put("path", path)
                    .put("storagePath", store.resolve(path).toString());
        }
        return input;
    }

    @Test
    void acmIsByteEqualToWriteOutCodeModelOfTheSameDirectoryInTwoRuns(@TempDir Path tmp) throws Exception {
        Path store = Fixtures.mappingFixture();
        CodeService service = Fixtures.service(store);
        ObjectNode input = codeModelInput(store);
        JsonNode first = service.codeModel(input);
        JsonNode second = service.codeModel(input);
        Fixtures.CONTRACTS.validate("skills.schema.json#/$defs/PluginCodeModelOutput", first);
        assertEquals(first, second);

        IdentifierProvider.reset();
        var extractor = new AllLanguagesExtractor(
                new CodeItemRepository(), store.toString(), Metamodel.CODE_WITH_COMPILATION_UNITS_AND_PACKAGES);
        Path file = tmp.resolve("codeModel.acm");
        extractor.writeOutCodeModel(extractor.extractModel(), file.toFile());
        String written = Files.readString(file);
        assertEquals(written, JsonMapper.shared().writeValueAsString(first.get("acm")));
        assertTrue(written.contains("\"language\":\"JAVA\""));
    }

    @Test
    void theElementsOfTheSameCallAreIndependentOfTheCodeModelCallBefore() throws Exception {
        Path store = Fixtures.mappingFixture();
        CodeService service = Fixtures.service(store);
        JsonNode before = service.extract(Fixtures.extractInput(store, null));
        service.codeModel(codeModelInput(store));
        assertEquals(before, service.extract(Fixtures.extractInput(store, null)));
    }
}
