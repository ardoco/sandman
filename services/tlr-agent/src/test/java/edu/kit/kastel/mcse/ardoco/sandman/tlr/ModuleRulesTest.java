package edu.kit.kastel.mcse.ardoco.sandman.tlr;

import static com.tngtech.archunit.lang.syntax.ArchRuleDefinition.noClasses;

import com.tngtech.archunit.core.importer.ClassFileImporter;
import org.junit.jupiter.api.Test;

/** R-030: LangChain4j only inside the ArDoCo approach package. */
class ModuleRulesTest {

    @Test
    void langchain4jOnlyInArdocoPackage() {
        var classes = new ClassFileImporter().importPackages("edu.kit.kastel.mcse.ardoco.sandman");
        noClasses()
                .that()
                .resideOutsideOfPackage("..sandman.tlr.ardoco..")
                .should()
                .dependOnClassesThat()
                .resideInAnyPackage("dev.langchain4j..")
                .check(classes);
    }
}
