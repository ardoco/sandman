package edu.kit.kastel.mcse.ardoco.sandman.tlr;

import static org.junit.jupiter.api.Assertions.assertThrows;

import com.tngtech.archunit.core.importer.ClassFileImporter;
import com.tngtech.archunit.lang.ArchRule;
import com.tngtech.archunit.lang.syntax.ArchRuleDefinition;
import edu.kit.kastel.mcse.ardoco.rulefixture.LangChain4jOutsideArdocoPackage;
import org.junit.jupiter.api.Test;

/** R-030: LangChain4j only inside the ArDoCo approach package. */
class ModuleRulesTest {

    private static final ArchRule LANGCHAIN4J_ONLY_IN_ARDOCO_PACKAGE = ArchRuleDefinition.noClasses()
            .that()
            .resideOutsideOfPackage("..sandman.tlr.ardoco..")
            .should()
            .dependOnClassesThat()
            .resideInAnyPackage("dev.langchain4j..");

    @Test
    void langchain4jOnlyInArdocoPackage() {
        var classes = new ClassFileImporter().importPackages("edu.kit.kastel.mcse.ardoco.sandman");
        LANGCHAIN4J_ONLY_IN_ARDOCO_PACKAGE.check(classes);
    }

    @Test
    void ruleFailsOnALangChain4jImportOutsideTheArdocoPackage() {
        var classes = new ClassFileImporter().importClasses(LangChain4jOutsideArdocoPackage.class);
        assertThrows(AssertionError.class, () -> LANGCHAIN4J_ONLY_IN_ARDOCO_PACKAGE.check(classes));
    }
}
