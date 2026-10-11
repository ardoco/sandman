package edu.kit.kastel.mcse.ardoco.sandman.tlr.ardoco;

import edu.kit.kastel.mcse.ardoco.core.api.text.Text;
import edu.kit.kastel.mcse.ardoco.core.textproviderjson.dto.TextDto;
import edu.kit.kastel.mcse.ardoco.sandman.agent.Ids;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.TlrContext;
import java.nio.charset.StandardCharsets;
import java.util.Iterator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CancellationException;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;

/**
 * CoreNLP answers for the last few rendered document versions, so that ardoco and ardocode annotate a version once.
 * The key is the rendered text and the provider identity: another provider gives another annotation. The cache keeps
 * the immutable answer (DTO) and builds a new {@link Text} for every call: ArDoCo's text objects fill lazy caches
 * (TextImpl.words, WordImpl.preWord and others) while a run reads them, so a run never shares one with another run.
 * The cache is bounded by entries and by words ({@value #MAX_WORDS_SETTING}); the skill clears it when a call ends.
 * Annotations of one key run once (the others wait and watch for cancellation); no lock is held across the HTTP call.
 */
final class TextCache {

    static final int MAX_ENTRIES = 4;
    static final String MAX_WORDS_SETTING = "SANDMAN_TEXT_CACHE_MAX_WORDS";
    static final long DEFAULT_MAX_WORDS = 100_000;

    private record Entry(TextDto dto, long words) {}

    private static final Object LOCK = new Object();
    private static final Map<String, Entry> CACHE = new LinkedHashMap<>(8, 0.75f, true);
    private static final Map<String, CompletableFuture<TextDto>> FLIGHTS = new LinkedHashMap<>();

    private TextCache() {}

    /** A new Text of the cached or freshly annotated lines. */
    static Text annotate(CorenlpClient client, CorenlpClient.Provider provider, List<String> lines, TlrContext ctx) {
        String key = provider.identity() + "\n"
                + Ids.sha256Hex(SentenceRendering.text(lines).getBytes(StandardCharsets.UTF_8));
        while (true) {
            CompletableFuture<TextDto> flight;
            boolean owner = false;
            synchronized (LOCK) {
                Entry cached = CACHE.get(key);
                if (cached != null) {
                    ctx.log().info("ardoco: annotation of {} sentences from the cache", lines.size());
                    return CorenlpClient.convert(cached.dto());
                }
                flight = FLIGHTS.get(key);
                if (flight == null) {
                    flight = new CompletableFuture<>();
                    FLIGHTS.put(key, flight);
                    owner = true;
                }
            }
            if (owner) {
                return CorenlpClient.convert(annotateAndStore(client, provider, lines, ctx, key, flight));
            }
            awaitFlight(flight, ctx);
        }
    }

    private static TextDto annotateAndStore(
            CorenlpClient client,
            CorenlpClient.Provider provider,
            List<String> lines,
            TlrContext ctx,
            String key,
            CompletableFuture<TextDto> flight) {
        try {
            long start = System.nanoTime();
            TextDto dto = client.annotateDto(lines, provider, ctx.cancelled());
            ctx.log()
                    .info(
                            "ardoco: annotated {} sentences in {} ms",
                            lines.size(),
                            (System.nanoTime() - start) / 1_000_000);
            store(key, dto);
            flight.complete(dto);
            return dto;
        } catch (Throwable t) {
            flight.completeExceptionally(t);
            throw t;
        } finally {
            synchronized (LOCK) {
                FLIGHTS.remove(key);
            }
        }
    }

    /** Waits for the owner; the caller tries again (cache, or becomes the owner) when the owner failed. */
    private static void awaitFlight(CompletableFuture<TextDto> flight, TlrContext ctx) {
        while (true) {
            try {
                flight.get(100, TimeUnit.MILLISECONDS);
                return;
            } catch (TimeoutException e) {
                if (ctx.cancelled().getAsBoolean()) {
                    throw new CancellationException("cancelled");
                }
            } catch (ExecutionException e) {
                return;
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                throw new CancellationException("interrupted");
            }
        }
    }

    private static void store(String key, TextDto dto) {
        long words =
                dto.getSentences().stream().mapToLong(s -> s.getWords().size()).sum();
        long max = Long.parseLong(CorenlpClient.setting(MAX_WORDS_SETTING, Long.toString(DEFAULT_MAX_WORDS)));
        synchronized (LOCK) {
            CACHE.put(key, new Entry(dto, words));
            long total = CACHE.values().stream().mapToLong(Entry::words).sum();
            for (Iterator<Entry> it = CACHE.values().iterator();
                    it.hasNext() && (CACHE.size() > MAX_ENTRIES || total > max); ) {
                total -= it.next().words();
                it.remove();
            }
        }
    }

    /** Drops all answers: the skill calls it when a call ends, and tests call it. */
    static void clear() {
        synchronized (LOCK) {
            CACHE.clear();
        }
    }
}
