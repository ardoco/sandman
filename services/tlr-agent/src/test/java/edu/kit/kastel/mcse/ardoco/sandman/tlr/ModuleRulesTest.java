package edu.kit.kastel.mcse.ardoco.sandman.tlr;

import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;

import com.tngtech.archunit.core.importer.ClassFileImporter;
import com.tngtech.archunit.lang.ArchRule;
import com.tngtech.archunit.lang.syntax.ArchRuleDefinition;
import edu.kit.kastel.mcse.ardoco.rulefixture.LangChain4jOutsideArdocoPackage;
import edu.kit.kastel.mcse.ardoco.rulefixture.StanfordPipelineOutsideProvider;
import org.junit.jupiter.api.Test;

/**
 * R-030: LangChain4j only inside the ArDoCo approach package. T2.5: no in-process CoreNLP pipeline and no models. T2.6: no
 * code extraction libraries (JDT, Magika, onnxruntime).
 */
class ModuleRulesTest {

    private static final ArchRule LANGCHAIN4J_ONLY_IN_ARDOCO_PACKAGE = ArchRuleDefinition.noClasses()
            .that()
            .resideOutsideOfPackage("..sandman.tlr.ardoco..")
            .should()
            .dependOnClassesThat()
            .resideInAnyPackage("dev.langchain4j..");

    private static final ArchRule NO_IN_PROCESS_CORENLP = ArchRuleDefinition.noClasses()
            .that()
            .resideInAPackage("edu.kit.kastel.mcse.ardoco.sandman..")
            .should()
            .dependOnClassesThat()
            .resideInAnyPackage("edu.stanford.nlp.pipeline..");

    private static ArchRule noDependencyOn(String... packages) {
        return ArchRuleDefinition.noClasses()
                .that()
                .resideInAPackage("edu.kit.kastel.mcse.ardoco.sandman..")
                .should()
                .dependOnClassesThat()
                .resideInAnyPackage(packages);
    }

    @Test
    void noClassOfSandmanUsesCodeExtractionLibraries() {
        var classes = new ClassFileImporter().importPackages("edu.kit.kastel.mcse.ardoco.sandman");
        noDependencyOn("org.eclipse.jdt..", "ai.onnxruntime..", "edu.kit.kastel.mcse.ardoco.magika..")
                .check(classes);
    }

    @Test
    void codeExtractionRuleFailsOnAViolation() {
        var classes = new ClassFileImporter().importClasses(StanfordPipelineOutsideProvider.class);
        assertThrows(
                AssertionError.class,
                () -> ArchRuleDefinition.noClasses()
                        .that()
                        .resideInAPackage("..rulefixture..")
                        .should()
                        .dependOnClassesThat()
                        .resideInAnyPackage("edu.stanford.nlp.pipeline..", "org.eclipse.jdt..")
                        .check(classes));
    }

    @Test
    void codeExtractionLibrariesAreNotOnTheRuntimeClasspath() {
        var loader = getClass().getClassLoader();
        assertNull(loader.getResource("org/eclipse/jdt/core/dom/ASTParser.class"));
        assertNull(loader.getResource("ai/onnxruntime/OrtEnvironment.class"));
        assertNull(loader.getResource("edu/kit/kastel/mcse/ardoco/magika/FileTypePredictor.class"));
    }

    @Test
    void noClassOfSandmanUsesTheCoreNlpPipeline() {
        var classes = new ClassFileImporter().importPackages("edu.kit.kastel.mcse.ardoco.sandman");
        NO_IN_PROCESS_CORENLP.check(classes);
    }

    @Test
    void coreNlpRuleFailsOnAPipelineImport() {
        var classes = new ClassFileImporter().importClasses(StanfordPipelineOutsideProvider.class);
        var rule = ArchRuleDefinition.noClasses()
                .that()
                .resideInAPackage("..rulefixture..")
                .should()
                .dependOnClassesThat()
                .resideInAnyPackage("edu.stanford.nlp.pipeline..");
        assertThrows(AssertionError.class, () -> rule.check(classes));
    }

    @Test
    void coreNlpModelsAreNotOnTheRuntimeClasspath() {
        var loader = getClass().getClassLoader();
        assertNull(loader.getResource("edu/stanford/nlp/models/"));
        assertNull(loader.getResource("edu/stanford/nlp/models/pos-tagger/english-left3words-distsim.tagger"));
        assertNull(loader.getResource("edu/stanford/nlp/models/lexparser/englishPCFG.ser.gz"));
    }

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
