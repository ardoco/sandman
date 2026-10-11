package edu.kit.kastel.mcse.ardoco.sandman.tlr.ardoco;

import edu.kit.kastel.mcse.ardoco.sandman.tlr.TlrContext;
import java.io.IOException;
import java.lang.management.ManagementFactory;
import java.lang.management.MemoryPoolMXBean;
import java.lang.management.MemoryType;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Comparator;
import java.util.List;
import java.util.concurrent.CancellationException;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.locks.ReentrantLock;
import java.util.stream.Stream;

/** What the approaches of this module share: ArDoCo keeps JVM-wide singletons (ConfigManager, SimilarityUtils), so one run at a time. */
final class ArdocoRuns {

    static final ReentrantLock LOCK = new ReentrantLock();

    private ArdocoRuns() {}

    /** Takes {@link #LOCK}; gives up with a CancellationException when the task is cancelled while waiting. */
    static void acquire(TlrContext ctx) {
        try {
            while (!LOCK.tryLock(100, TimeUnit.MILLISECONDS)) {
                if (ctx.cancelled().getAsBoolean()) {
                    throw new CancellationException("cancelled");
                }
            }
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new CancellationException("interrupted");
        }
    }

    /** Resets the heap peak, for {@link #peakHeapMiB()}; runs are one at a time. */
    static void resetHeapPeak() {
        heapPools().forEach(MemoryPoolMXBean::resetPeakUsage);
    }

    static long peakHeapMiB() {
        return heapPools().stream().mapToLong(p -> p.getPeakUsage().getUsed()).sum() >> 20;
    }

    private static List<MemoryPoolMXBean> heapPools() {
        return ManagementFactory.getMemoryPoolMXBeans().stream()
                .filter(p -> p.getType() == MemoryType.HEAP)
                .toList();
    }

    static void delete(Path dir, TlrContext ctx) {
        try (Stream<Path> files = Files.walk(dir)) {
            files.sorted(Comparator.reverseOrder()).forEach(p -> {
                if (!p.toFile().delete()) {
                    ctx.log().warn("ardoco: could not delete {}", p);
                }
            });
        } catch (IOException e) {
            ctx.log().warn("ardoco: could not clean up temp dir {}", dir, e);
        }
    }
}
