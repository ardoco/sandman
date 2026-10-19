package edu.kit.kastel.mcse.ardoco.sandman.tlr.ardoco;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotSame;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import edu.kit.kastel.mcse.ardoco.core.api.text.Text;
import edu.kit.kastel.mcse.ardoco.core.textproviderjson.dto.TextDto;
import edu.kit.kastel.mcse.ardoco.sandman.ardocotext.CorenlpClient;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.TlrContext;
import java.time.Duration;
import java.util.List;
import java.util.concurrent.CancellationException;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.function.BooleanSupplier;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.slf4j.LoggerFactory;
import tools.jackson.databind.json.JsonMapper;

class TextCacheTest {

    private final AtomicInteger requests = new AtomicInteger();
    private volatile CountDownLatch gate;
    private volatile CountDownLatch entered;
    private volatile boolean failNext;

    /** One word per sentence, which is the line itself (as in CorenlpClientTest). */
    private static TextDto dto(List<String> lines) {
        var out = new StringBuilder("{\"sentences\":[");
        for (int i = 0; i < lines.size(); i++) {
            String s = lines.get(i).replace(" ", "");
            out.append(i > 0 ? "," : "")
                    .append("{\"constituencyTree\":\"(ROOT (NN ")
                    .append(s)
                    .append("))\",\"sentenceNo\":")
                    .append(i + 1)
                    .append(",\"text\":\"")
                    .append(s)
                    .append("\",\"words\":[{\"id\":1,\"incomingDependencies\":[],\"lemma\":\"")
                    .append(s.toLowerCase())
                    .append("\",\"outgoingDependencies\":[],\"sentenceNo\":")
                    .append(i + 1)
                    .append(",\"text\":\"")
                    .append(s)
                    .append("\",\"posTag\":\"NN\"}]}");
        }
        return JsonMapper.builder().build().readValue(out.append("]}").toString(), TextDto.class);
    }

    private final CorenlpClient counting =
            new CorenlpClient(
                    java.net.URI.create("http://unused"),
                    Duration.ofSeconds(1),
                    CorenlpClient.Eolonly.AUTO,
                    List.of(),
                    null) {
                @Override
                public TextDto annotateDto(List<String> lines, Provider provider, BooleanSupplier cancelled) {
                    requests.incrementAndGet();
                    CountDownLatch in = entered;
                    if (in != null) {
                        in.countDown();
                    }
                    CountDownLatch g = gate;
                    if (g != null && lines.get(0).equals("slow")) {
                        try {
                            g.await(10, TimeUnit.SECONDS);
                        } catch (InterruptedException e) {
                            throw new CancellationException("interrupted");
                        }
                    }
                    if (failNext) {
                        failNext = false;
                        throw new IllegalStateException("annotation failed");
                    }
                    return dto(lines);
                }
            };

    private final TlrContext ctx = context(() -> false);

    private static TlrContext context(BooleanSupplier cancelled) {
        return new TlrContext(
                null,
                null,
                cancelled,
                LoggerFactory.getLogger(TextCacheTest.class),
                JsonMapper.shared().createObjectNode());
    }

    @BeforeEach
    @AfterEach
    void clear() {
        TextCache.clear();
        System.clearProperty(TextCache.MAX_WORDS_SETTING);
        requests.set(0);
        gate = null;
        entered = null;
        failNext = false;
    }

    private Text annotate(String provider, String... lines) {
        return TextCache.annotate(counting, new CorenlpClient.Provider(true, provider), List.of(lines), ctx);
    }

    @Test
    void theSameRenderedTextAndProviderIsAnnotatedOnceAndEveryCallGetsItsOwnText() {
        Text first = annotate("v1", "A sentence.", "Another one.");
        Text second = annotate("v1", "A sentence.", "Another one.");
        assertEquals(1, requests.get());
        assertNotSame(first, second);
        assertNotSame(first.getSentences().get(0), second.getSentences().get(0));
        assertNotSame(first.getWord(0), second.getWord(0));
        assertEquals(first.getWord(1).getText(), second.getWord(1).getText());
        assertEquals(first.getSentences().size(), second.getSentences().size());
    }

    @Test
    void anotherTextOrProviderIsAnnotatedAgain() {
        annotate("v1", "A sentence.");
        annotate("v2", "A sentence.");
        annotate("v1", "Other sentence.");
        assertEquals(3, requests.get());
    }

    @Test
    void theCacheKeepsAtMostFourEntriesAndDropsTheLeastRecentlyUsed() {
        for (int i = 0; i < TextCache.MAX_ENTRIES; i++) {
            annotate("v", "text " + i);
        }
        annotate("v", "text 0"); // 0 is now the most recent, 1 the oldest
        annotate("v", "text 4"); // evicts 1
        assertEquals(TextCache.MAX_ENTRIES + 1, requests.get());
        annotate("v", "text 0");
        annotate("v", "text 4");
        assertEquals(TextCache.MAX_ENTRIES + 1, requests.get());
        annotate("v", "text 1");
        assertEquals(TextCache.MAX_ENTRIES + 2, requests.get());
    }

    @Test
    void theCacheIsBoundedByTheNumberOfWords() {
        System.setProperty(TextCache.MAX_WORDS_SETTING, "5");
        annotate("v", "a1", "a2", "a3"); // 3 words
        annotate("v", "b1", "b2", "b3"); // 6 words in total: the oldest goes
        assertEquals(2, requests.get());
        annotate("v", "b1", "b2", "b3");
        assertEquals(2, requests.get(), "the newest stays");
        annotate("v", "a1", "a2", "a3");
        assertEquals(3, requests.get(), "the oldest was dropped");
        System.setProperty(TextCache.MAX_WORDS_SETTING, "2");
        annotate("v", "c1", "c2", "c3");
        annotate("v", "c1", "c2", "c3");
        assertEquals(5, requests.get(), "a text above the bound is not kept");
    }

    @Test
    void callFinishedOfBothApproachesDropsTheCache() {
        annotate("v", "A sentence.");
        new ArdocoApproach().callFinished();
        annotate("v", "A sentence.");
        new ArdocodeApproach().callFinished();
        annotate("v", "A sentence.");
        assertEquals(3, requests.get());
    }

    @Test
    void noLockIsHeldWhileOneKeyIsAnnotated() throws Exception {
        gate = new CountDownLatch(1);
        entered = new CountDownLatch(1);
        CompletableFuture<Text> slow = CompletableFuture.supplyAsync(() -> annotate("v", "slow"));
        assertTrue(entered.await(5, TimeUnit.SECONDS));
        annotate("v", "other"); // would block behind a lock held across the call
        assertTrue(!slow.isDone());
        gate.countDown();
        slow.get(5, TimeUnit.SECONDS);
    }

    @Test
    void oneKeyIsAnnotatedOnceForConcurrentCallers() throws Exception {
        gate = new CountDownLatch(1);
        entered = new CountDownLatch(1);
        CompletableFuture<Text> a = CompletableFuture.supplyAsync(() -> annotate("v", "slow"));
        assertTrue(entered.await(5, TimeUnit.SECONDS));
        CompletableFuture<Text> b = CompletableFuture.supplyAsync(() -> annotate("v", "slow"));
        Thread.sleep(300);
        assertTrue(!b.isDone());
        gate.countDown();
        a.get(5, TimeUnit.SECONDS);
        b.get(5, TimeUnit.SECONDS);
        assertEquals(1, requests.get());
    }

    @Test
    void aWaitingCallerWatchesForCancellation() throws Exception {
        gate = new CountDownLatch(1);
        entered = new CountDownLatch(1);
        CompletableFuture<Text> owner = CompletableFuture.supplyAsync(() -> annotate("v", "slow"));
        assertTrue(entered.await(5, TimeUnit.SECONDS));
        var cancelled = new java.util.concurrent.atomic.AtomicBoolean();
        CompletableFuture<Text> waiter = CompletableFuture.supplyAsync(() -> TextCache.annotate(
                counting, new CorenlpClient.Provider(true, "v"), List.of("slow"), context(cancelled::get)));
        Thread.sleep(200);
        cancelled.set(true);
        var e = assertThrows(java.util.concurrent.ExecutionException.class, () -> waiter.get(5, TimeUnit.SECONDS));
        assertTrue(e.getCause() instanceof CancellationException, String.valueOf(e.getCause()));
        gate.countDown();
        owner.get(5, TimeUnit.SECONDS);
    }

    @Test
    void aFailedAnnotationIsNotCachedAndAWaiterTriesItselfAgain() throws Exception {
        gate = new CountDownLatch(1);
        entered = new CountDownLatch(1);
        failNext = true;
        CompletableFuture<Text> owner = CompletableFuture.supplyAsync(() -> annotate("v", "slow"));
        assertTrue(entered.await(5, TimeUnit.SECONDS));
        CompletableFuture<Text> waiter = CompletableFuture.supplyAsync(() -> annotate("v", "slow"));
        Thread.sleep(300);
        gate.countDown();
        assertThrows(java.util.concurrent.ExecutionException.class, () -> owner.get(5, TimeUnit.SECONDS));
        assertTrue(waiter.get(5, TimeUnit.SECONDS) != null);
        assertEquals(2, requests.get());
    }
}
