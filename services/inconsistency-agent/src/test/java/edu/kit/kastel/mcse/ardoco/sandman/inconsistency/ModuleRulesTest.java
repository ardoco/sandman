package edu.kit.kastel.mcse.ardoco.sandman.inconsistency;

import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.tngtech.archunit.core.importer.ClassFileImporter;
import com.tngtech.archunit.lang.ArchRule;
import com.tngtech.archunit.lang.syntax.ArchRuleDefinition;
import edu.kit.kastel.mcse.ardoco.rulefixture.LangChain4jOutsideArdocoPackage;
import org.junit.jupiter.api.Test;

/** R-030: LangChain4j only inside the ArDoCo detector package. */
class ModuleRulesTest {

    private static final ArchRule LANGCHAIN4J_ONLY_IN_ARDOCO_PACKAGE = ArchRuleDefinition.noClasses()
            .that()
            .resideOutsideOfPackage("..sandman.inconsistency.ardoco..")
            .should()
            .dependOnClassesThat()
            .resideInAnyPackage("dev.langchain4j..");

    @Test
    void langchain4jOnlyInArdocoPackage() {
        var classes = new ClassFileImporter().importPackages("edu.kit.kastel.mcse.ardoco.sandman");
        assertTrue(classes.size() > 0);
        LANGCHAIN4J_ONLY_IN_ARDOCO_PACKAGE.check(classes);
    }

    @Test
    void ruleFailsOnALangChain4jImportOutsideTheArdocoPackage() {
        var classes = new ClassFileImporter().importClasses(LangChain4jOutsideArdocoPackage.class);
        assertThrows(AssertionError.class, () -> LANGCHAIN4J_ONLY_IN_ARDOCO_PACKAGE.check(classes));
    }
}
