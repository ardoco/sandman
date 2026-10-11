package edu.kit.kastel.mcse.ardoco.sandman.tlr.ardoco;

import edu.kit.kastel.mcse.ardoco.core.api.text.Text;
import edu.kit.kastel.mcse.ardoco.sandman.agent.Ids;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.TlrContext;
import java.nio.charset.StandardCharsets;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Annotated texts of the last few rendered document versions, so that ardoco and ardocode annotate a version once.
 * The key is the rendered text and the provider identity: another provider gives another annotation.
 */
final class TextCache {

    static final int MAX_ENTRIES = 4;
    private static final Map<String, Text> CACHE = new LinkedHashMap<>(8, 0.75f, true) {
        @Override
        protected boolean removeEldestEntry(Map.Entry<String, Text> eldest) {
            return size() > MAX_ENTRIES;
        }
    };

    private TextCache() {}

    /** The cached annotation of the lines, else the provider's; ponytail: one lock, annotations run one at a time. */
    static synchronized Text annotate(
            CorenlpClient client, CorenlpClient.Provider provider, List<String> lines, TlrContext ctx) {
        String key = provider.identity() + "\n"
                + Ids.sha256Hex(SentenceRendering.text(lines).getBytes(StandardCharsets.UTF_8));
        Text cached = CACHE.get(key);
        if (cached != null) {
            ctx.log().info("ardoco: annotation of {} sentences from the cache", lines.size());
            return cached;
        }
        long start = System.nanoTime();
        Text text = client.annotate(lines, provider, ctx.cancelled());
        ctx.log()
                .info("ardoco: annotated {} sentences in {} ms", lines.size(), (System.nanoTime() - start) / 1_000_000);
        CACHE.put(key, text);
        return text;
    }

    /** For tests. */
    static synchronized void clear() {
        CACHE.clear();
    }
}
