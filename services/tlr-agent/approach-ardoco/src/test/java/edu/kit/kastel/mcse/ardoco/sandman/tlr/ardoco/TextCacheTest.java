package edu.kit.kastel.mcse.ardoco.sandman.tlr.ardoco;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotSame;
import static org.junit.jupiter.api.Assertions.assertSame;

import edu.kit.kastel.mcse.ardoco.core.api.text.Text;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.TlrContext;
import java.lang.reflect.Proxy;
import java.time.Duration;
import java.util.List;
import java.util.function.BooleanSupplier;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.slf4j.LoggerFactory;
import tools.jackson.databind.json.JsonMapper;

class TextCacheTest {

    private int requests;

    private final CorenlpClient counting =
            new CorenlpClient(
                    java.net.URI.create("http://unused"),
                    Duration.ofSeconds(1),
                    CorenlpClient.Eolonly.AUTO,
                    List.of(),
                    null) {
                @Override
                Text annotate(List<String> lines, Provider provider, BooleanSupplier cancelled) {
                    requests++;
                    return (Text) Proxy.newProxyInstance(
                            Text.class.getClassLoader(), new Class<?>[] {Text.class}, (p, m, a) -> null);
                }
            };

    private final TlrContext ctx = new TlrContext(
            null,
            null,
            () -> false,
            LoggerFactory.getLogger(TextCacheTest.class),
            JsonMapper.shared().createObjectNode());

    @BeforeEach
    void clear() {
        TextCache.clear();
        requests = 0;
    }

    private Text annotate(String provider, String... lines) {
        return TextCache.annotate(counting, new CorenlpClient.Provider(true, provider), List.of(lines), ctx);
    }

    @Test
    void theSameRenderedTextAndProviderIsAnnotatedOnce() {
        Text first = annotate("v1", "A sentence.", "Another one.");
        assertSame(first, annotate("v1", "A sentence.", "Another one."));
        assertEquals(1, requests);
    }

    @Test
    void anotherTextOrProviderIsAnnotatedAgain() {
        Text first = annotate("v1", "A sentence.");
        assertNotSame(first, annotate("v2", "A sentence."));
        assertNotSame(first, annotate("v1", "Other sentence."));
        assertEquals(3, requests);
    }

    @Test
    void theCacheKeepsAtMostFourEntriesAndDropsTheLeastRecentlyUsed() {
        for (int i = 0; i < TextCache.MAX_ENTRIES; i++) {
            annotate("v", "text " + i);
        }
        annotate("v", "text 0"); // 0 is now the most recent, 1 the oldest
        annotate("v", "text 4"); // evicts 1
        assertEquals(TextCache.MAX_ENTRIES + 1, requests);
        annotate("v", "text 0");
        annotate("v", "text 4");
        assertEquals(TextCache.MAX_ENTRIES + 1, requests);
        annotate("v", "text 1");
        assertEquals(TextCache.MAX_ENTRIES + 2, requests);
    }
}
