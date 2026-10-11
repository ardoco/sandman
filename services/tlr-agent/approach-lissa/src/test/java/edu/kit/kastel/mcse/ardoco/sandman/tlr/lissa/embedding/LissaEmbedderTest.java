package edu.kit.kastel.mcse.ardoco.sandman.tlr.lissa.embedding;

import static org.junit.jupiter.api.Assertions.assertArrayEquals;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import edu.kit.kastel.mcse.ardoco.sandman.tlr.lissa.FakeGateway;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.lissa.knowledge.Element;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.CancellationException;
import org.junit.jupiter.api.Test;

class LissaEmbedderTest {

    private static Element element(String id, String content) {
        return new Element(id, "requirement", content, 0, null, true);
    }

    @Test
    void embedsOnePerElementInInputOrder() {
        try (FakeGateway gateway = new FakeGateway()) {
            gateway.embedAnswer =
                    body -> new float[] {body.get("input").asString().length(), 1f};
            var result = new LissaEmbedder(gateway.client(), "embed-default", 4, () -> false, w -> {})
                    .embed(List.of(element("a", "xx"), element("b", "xxxx"), element("c", "x")));
            assertEquals(3, gateway.embedBodies.size());
            assertEquals(
                    List.of("a", "b", "c"),
                    result.stream().map(p -> p.first().getIdentifier()).toList());
            assertArrayEquals(new float[] {4f, 1f}, result.get(1).second());
        }
    }

    @Test
    void skipsOverLimitInputWithWarning() {
        try (FakeGateway gateway = new FakeGateway()) {
            gateway.embedStatus = 400;
            gateway.embedErrorBody = "{\"error\":{\"code\":\"context_length_exceeded\"}}";
            List<String> warnings = new ArrayList<>();
            var result = new LissaEmbedder(gateway.client(), "embed-default", 1, () -> false, warnings::add)
                    .embed(List.of(element("a", "xx")));
            assertEquals(0, result.size());
            assertEquals(1, warnings.size());
            assertEquals(true, warnings.get(0).contains("a"));
        }
    }

    @Test
    void otherFailuresPropagate() {
        try (FakeGateway gateway = new FakeGateway()) {
            gateway.embedStatus = 400;
            var embedder = new LissaEmbedder(gateway.client(), "embed-default", 1, () -> false, w -> {});
            assertThrows(RuntimeException.class, () -> embedder.embed(List.of(element("a", "xx"))));
        }
    }

    @Test
    void cancellationStopsCalls() {
        try (FakeGateway gateway = new FakeGateway()) {
            var embedder = new LissaEmbedder(gateway.client(), "embed-default", 1, () -> true, w -> {});
            assertThrows(CancellationException.class, () -> embedder.embed(List.of(element("a", "xx"))));
            assertEquals(0, gateway.embedBodies.size());
        }
    }
}
