package edu.kit.kastel.mcse.ardoco.sandman.tlr.ardoco;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import edu.kit.kastel.mcse.ardoco.sandman.ardocotext.CorenlpClient;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.TlrContext;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.TlrInput;
import java.util.List;
import java.util.concurrent.CancellationException;
import java.util.function.BooleanSupplier;
import org.junit.jupiter.api.Test;
import org.slf4j.LoggerFactory;
import tools.jackson.databind.json.JsonMapper;

class ArdocoApproachTest {

    private static final String TEXT = "The MediaCache stores audio files.";

    private static TlrInput input(String... modelKeys) {
        var doc =
                new TlrInput.Version("ver_doc", "art_doc", "sad.md", "architecture-doc", "markdown", "artifact://p/d");
        var model = new TlrInput.Version(
                "ver_model", "art_model", "m.repository", "architecture-model", "pcm-repository", "artifact://p/m");
        var sentence = new TlrInput.Element(
                "el_s0",
                "ver_doc",
                "art_doc#sec:/" + edu.kit.kastel.mcse.ardoco.sandman.ardocotext.SentenceRendering.hash(TEXT),
                "sec:/" + edu.kit.kastel.mcse.ardoco.sandman.ardocotext.SentenceRendering.hash(TEXT),
                "TextUnit",
                "sentence",
                TEXT,
                TEXT,
                0,
                "architecture-doc");
        var targets = java.util.Arrays.stream(modelKeys)
                .map(k -> new TlrInput.Element(
                        "el_" + k + Math.abs(System.identityHashCode(k)),
                        "ver_model",
                        "art_model#" + k,
                        k,
                        "ModelElement",
                        "component",
                        k,
                        null,
                        null,
                        "architecture-model"))
                .toList();
        return new TlrInput("p", List.of(doc, model), List.of(sentence), targets);
    }

    private static TlrContext ctx(BooleanSupplier cancelled) {
        return new TlrContext(
                null,
                null,
                cancelled,
                LoggerFactory.getLogger(ArdocoApproachTest.class),
                JsonMapper.shared().createObjectNode());
    }

    @Test
    void duplicateModelLocalKeyFailsTheRun() {
        var e = assertThrows(
                IllegalStateException.class, () -> new ArdocoApproach().recover(input("abc", "abc"), ctx(() -> false)));
        assertTrue(e.getMessage().contains("two elements with local key abc"), e.getMessage());
    }

    @Test
    void cancellationBeforeAPairIsACancellationNotAFailure() {
        assertThrows(CancellationException.class, () -> new ArdocoApproach().recover(input("abc"), ctx(() -> true)));
    }

    @Test
    void acceptsNoParameters() {
        assertEquals(java.util.Set.of(), new ArdocoApproach().paramKeys());
    }

    private static CorenlpClient deadClient() throws java.io.IOException {
        int port;
        try (var socket = new java.net.ServerSocket(0)) {
            port = socket.getLocalPort();
        }
        return new CorenlpClient(
                java.net.URI.create("http://127.0.0.1:" + port),
                java.time.Duration.ofSeconds(2),
                CorenlpClient.Eolonly.AUTO,
                List.of(java.time.Duration.ZERO, java.time.Duration.ZERO),
                null);
    }

    @Test
    void unreachableProviderIsARetryableFailureForRecoverAndEnvironment() throws Exception {
        var approach = new ArdocoApproach(deadClient());
        assertThrows(
                edu.kit.kastel.mcse.ardoco.sandman.tlr.ApproachUnavailableException.class,
                () -> approach.recover(input("abc"), ctx(() -> false)));
        assertThrows(
                edu.kit.kastel.mcse.ardoco.sandman.tlr.ApproachUnavailableException.class,
                () -> approach.environment(ctx(() -> false)));
    }

    @Test
    void versionIncreasedForTheProviderChange() {
        assertEquals("2.1.0+a2", ArdocoApproach.VERSION);
    }
}
