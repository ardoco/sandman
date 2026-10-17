package edu.kit.kastel.mcse.ardoco.sandman.tlr.ardoco;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import edu.kit.kastel.mcse.ardoco.core.api.models.code.CodeCompilationUnit;
import edu.kit.kastel.mcse.ardoco.core.api.models.code.CodeItemRepository;
import edu.kit.kastel.mcse.ardoco.core.api.models.code.ProgrammingLanguage;
import edu.kit.kastel.mcse.ardoco.sandman.ardocotext.CorenlpClient;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.CandidateLink;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.TlrContext;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.TlrInput;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeSet;
import org.junit.jupiter.api.Test;
import org.slf4j.LoggerFactory;
import tools.jackson.databind.json.JsonMapper;

class ArdocodeApproachTest {

    private static TlrInput.Element sentence(int i) {
        return new TlrInput.Element(
                "el_s" + i, "ver_doc", "k" + i, "k" + i, "TextUnit", "sentence", "s", "s", i, "architecture-doc");
    }

    private static TlrInput.Element file(String path) {
        return new TlrInput.Element(
                "el_" + path, "ver_code", "art#" + path, path, "CodeUnit", "file", path, null, null, "code");
    }

    private static Map<String, TlrInput.Element> files(String... paths) {
        Map<String, TlrInput.Element> m = new LinkedHashMap<>();
        for (String p : paths) {
            m.put(p, file(p));
        }
        return m;
    }

    private static CodeCompilationUnit unit(ProgrammingLanguage language, List<String> dirs, String name, String ext) {
        return new CodeCompilationUnit(new CodeItemRepository(), name, new TreeSet<>(), dirs, ext, language, List.of());
    }

    @Test
    void javaPathsAreDecodedWholeShellPathsOnlyUpToTheFileName() {
        assertEquals(
                "dir with space/a b/My File.java",
                ArdocodeApproach.pathOf(
                        unit(ProgrammingLanguage.JAVA, List.of("dir%20with%20space", "a%20b"), "My%20File", "java")));
        assertEquals(
                "100% dir/a b/100%.java",
                ArdocodeApproach.pathOf(
                        unit(ProgrammingLanguage.JAVA, List.of("100%25%20dir", "a%20b"), "100%25", "java")));
        assertEquals(
                "100% dir/a b/100%.sh",
                ArdocodeApproach.pathOf(
                        unit(ProgrammingLanguage.SHELL, List.of("100%25%20dir", "a%20b"), "100%", "sh")));
        assertEquals(
                "x/run%20it.sh",
                ArdocodeApproach.pathOf(unit(ProgrammingLanguage.SHELL, List.of("x"), "run%20it", "sh")));
        assertEquals(
                "a+b/ü.java",
                ArdocodeApproach.pathOf(unit(ProgrammingLanguage.JAVA, List.of("a+b"), "%C3%BC", "java")));
        assertEquals("%E0%A4%A.java", ArdocodeApproach.decode("%E0%A4%A.java"));
    }

    @Test
    void oneLinkPerDistinctSentenceAndFileTargetingTheFileElement() {
        var sentences = List.of(sentence(0), sentence(1));
        var pairs = List.of(
                Map.entry(1, "b/B.java"), Map.entry(0, "a/A.java"), Map.entry(1, "b/B.java"), Map.entry(0, "b/B.java"));
        List<CandidateLink> links = ArdocodeApproach.toLinks(pairs, sentences, files("a/A.java", "b/B.java"));
        assertEquals(
                List.of(
                        new CandidateLink("el_s0", "el_a/A.java", "documents", 1.0, null),
                        new CandidateLink("el_s0", "el_b/B.java", "documents", 1.0, null),
                        new CandidateLink("el_s1", "el_b/B.java", "documents", 1.0, null)),
                links);
    }

    @Test
    void anUnknownPathOrSentenceFailsTheRun() {
        var sentences = List.of(sentence(0));
        var unknownPath = assertThrows(
                IllegalStateException.class,
                () -> ArdocodeApproach.toLinks(List.of(Map.entry(0, "x/X.java")), sentences, files("a/A.java")));
        assertTrue(unknownPath.getMessage().contains("x/X.java"), unknownPath.getMessage());
        assertThrows(
                IllegalStateException.class,
                () -> ArdocodeApproach.toLinks(List.of(Map.entry(3, "a/A.java")), sentences, files("a/A.java")));
    }

    @Test
    void supportsDocumentsToCodeAsOneWholeProjectRunWithoutParameters() {
        var approach = new ArdocodeApproach();
        assertEquals("ardocode", approach.id());
        assertEquals("2.1.0+a1", approach.version());
        assertEquals(
                Set.of(new edu.kit.kastel.mcse.ardoco.sandman.tlr.KindPair("architecture-doc", "code")),
                approach.supports());
        assertEquals(edu.kit.kastel.mcse.ardoco.sandman.tlr.ScopeMode.WHOLE_PROJECT, approach.scopeMode());
        assertEquals(Set.of(), approach.paramKeys());
        assertEquals(false, approach.usesLlm());
    }

    @Test
    void withoutCodeOrDocumentsThereIsNothingToLinkAndNoPluginCall() {
        var ctx = new TlrContext(
                null,
                null,
                () -> false,
                LoggerFactory.getLogger(ArdocodeApproachTest.class),
                JsonMapper.shared().createObjectNode());
        var doc =
                new TlrInput.Version("ver_doc", "art_doc", "sad.md", "architecture-doc", "markdown", "artifact://p/d");
        var code = new TlrInput.Version("ver_code", "art_code", "A.java", "code", null, "artifact://p/c");
        // clients are null: a call would fail with a connection error, not return an empty list
        var approach = new ArdocodeApproach(null, null);
        assertEquals(
                List.of(), approach.recover(new TlrInput("p", List.of(doc), List.of(sentence(0)), List.of()), ctx));
        assertEquals(
                List.of(), approach.recover(new TlrInput("p", List.of(code), List.of(), List.of(file("A.java"))), ctx));
    }

    private static CodeModelClient plugin(java.util.function.Supplier<String> identity) {
        return new CodeModelClient(
                "http://plugin", java.time.Duration.ofSeconds(5), java.time.Duration.ZERO, new CodeModelClient.Calls() {
                    @Override
                    public String identity(String url) {
                        return identity.get();
                    }

                    @Override
                    public edu.kit.kastel.mcse.ardoco.sandman.agent.a2a.A2aClient.TaskSnapshot submit(
                            String url, tools.jackson.databind.JsonNode input, String messageId) {
                        throw new IllegalStateException();
                    }

                    @Override
                    public edu.kit.kastel.mcse.ardoco.sandman.agent.a2a.A2aClient.TaskSnapshot get(
                            String url, String taskId) {
                        throw new IllegalStateException();
                    }

                    @Override
                    public edu.kit.kastel.mcse.ardoco.sandman.agent.a2a.A2aClient.TaskSnapshot cancel(
                            String url, String taskId) {
                        throw new IllegalStateException();
                    }
                });
    }

    private static CorenlpClient corenlp(java.util.function.Supplier<CorenlpClient.Provider> provider) {
        return new CorenlpClient(
                java.net.URI.create("http://127.0.0.1:1"),
                java.time.Duration.ofSeconds(1),
                CorenlpClient.Eolonly.AUTO,
                List.of(),
                null) {
            @Override
            public Provider provider(java.util.function.BooleanSupplier cancelled) {
                return provider.get();
            }
        };
    }

    private static TlrContext envCtx() {
        return new TlrContext(
                null,
                null,
                () -> false,
                LoggerFactory.getLogger(ArdocodeApproachTest.class),
                JsonMapper.shared().createObjectNode());
    }

    @Test
    void environmentNamesTheProviderAndThePlugin() {
        var approach = new ArdocodeApproach(
                corenlp(() -> new CorenlpClient.Provider(true, "v1")), plugin(() -> "code-extractor-ardoco@2.1.0+c1"));
        assertEquals(
                "provider=v1;eolonly=true;codeModel=code-extractor-ardoco@2.1.0+c1", approach.environment(envCtx()));
    }

    @Test
    void environmentOfADownProviderOrPluginIsUnavailableNotAPlaceholder() {
        var providerDown = new ArdocodeApproach(
                corenlp(() -> {
                    throw new edu.kit.kastel.mcse.ardoco.sandman.tlr.ApproachUnavailableException("down", null);
                }),
                plugin(() -> "p@1"));
        assertThrows(
                edu.kit.kastel.mcse.ardoco.sandman.tlr.ApproachUnavailableException.class,
                () -> providerDown.environment(envCtx()));
        var pluginDown = new ArdocodeApproach(corenlp(() -> new CorenlpClient.Provider(true, "v1")), plugin(() -> {
            throw new IllegalStateException("refused");
        }));
        assertThrows(
                edu.kit.kastel.mcse.ardoco.sandman.tlr.ApproachUnavailableException.class,
                () -> pluginDown.environment(envCtx()));
    }

    @Test
    void environmentLetsACancellationPass() {
        var approach = new ArdocodeApproach(
                corenlp(() -> {
                    throw new java.util.concurrent.CancellationException("cancelled");
                }),
                plugin(() -> "p@1"));
        assertThrows(java.util.concurrent.CancellationException.class, () -> approach.environment(envCtx()));
    }
}
