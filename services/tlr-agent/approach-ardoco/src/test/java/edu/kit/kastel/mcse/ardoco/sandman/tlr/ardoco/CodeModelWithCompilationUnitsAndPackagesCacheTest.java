package edu.kit.kastel.mcse.ardoco.sandman.tlr.ardoco;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotSame;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.fasterxml.jackson.datatype.jdk8.Jdk8Module;
import edu.kit.kastel.mcse.ardoco.core.api.models.CodeModel;
import edu.kit.kastel.mcse.ardoco.core.api.models.CodeModelWithCompilationUnitsAndPackages;
import edu.kit.kastel.mcse.ardoco.core.common.JsonHandling;
import java.io.IOException;
import java.nio.file.Path;
import java.util.ArrayList;
import org.junit.jupiter.api.Test;

class CodeModelWithCompilationUnitsAndPackagesCacheTest {

    @Test
    void theClassOfThisModuleIsLoaded() throws Exception {
        Path origin = Path.of(CodeModelWithCompilationUnitsAndPackages.class
                .getProtectionDomain()
                .getCodeSource()
                .getLocation()
                .toURI());
        assertTrue(origin.toString().contains("ardoco-shadow"), origin.toString());
    }

    @Test
    void endpointsAndTypeIdentifiersAreStableCopies() throws IOException {
        var mapper = JsonHandling.createObjectMapper();
        mapper.registerModule(new Jdk8Module());
        var model = new CodeModelWithCompilationUnitsAndPackages(
                mapper.readValue(CodeModels.acm("mediastore-code-model").toFile(), CodeModel.CodeModelDto.class));
        var endpoints = model.getEndpoints();
        var identifiers = model.getTypeIdentifiers();
        assertFalse(endpoints.isEmpty());
        assertEquals(endpoints, model.getContent());
        assertNotSame(endpoints, model.getEndpoints());
        assertEquals(endpoints, model.getEndpoints());
        assertNotSame(identifiers, model.getTypeIdentifiers());
        assertEquals(identifiers, model.getTypeIdentifiers());
        new ArrayList<>(endpoints).clear();
        endpoints.clear();
        identifiers.clear();
        assertFalse(model.getEndpoints().isEmpty());
        assertFalse(model.getTypeIdentifiers().isEmpty());
    }
}
