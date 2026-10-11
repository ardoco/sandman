package edu.kit.kastel.mcse.ardoco.sandman.code;

import static org.junit.jupiter.api.Assertions.assertTrue;

import java.lang.management.ManagementFactory;
import java.lang.management.MemoryPoolMXBean;
import java.lang.management.MemoryType;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.concurrent.atomic.AtomicLong;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import tools.jackson.databind.JsonNode;

/**
 * The worst input within the default caps must not exhaust the container heap (-Xmx2560m, see the benchmark profile):
 * all bytes are dense Java, which needs the most heap per byte because ArDoCo keeps every AST until the model is
 * built. The input is either maximal bytes in files of the per-file cap or the maximal file count. Heap exhaustion
 * would make the service drain and restart. Only in profile benchmark.
 */
@Tag("benchmark")
class MaxCapInputTest {

    @TempDir
    Path tmp;

    private static String denseSource(long bytes, int index) throws Exception {
        String outer = Files.readString(
                Path.of(MaxCapInputTest.class.getResource("/fixtures/mapping/com/acme/Outer.java").toURI()));
        int open = outer.indexOf('{', outer.indexOf("class Outer")) + 1;
        String members = outer.substring(open, outer.lastIndexOf('}'));
        StringBuilder out = new StringBuilder("package p" + index + ";\n\npublic class D" + index + " {\n");
        while (out.length() + members.length() < bytes) {
            out.append(members);
        }
        return out.append("}\n").toString();
    }

    private void run(String label, int files, long bytesPerFile) throws Exception {
        Path store = Files.createDirectories(tmp.resolve("store"));
        String source = null;
        for (int i = 0; i < files; i++) {
            source = i == 0 || bytesPerFile > 100_000 ? denseSource(bytesPerFile, i) : source.replace("package p" + (i - 1) + ";", "package p" + i + ";").replace("class D" + (i - 1) + " ", "class D" + i + " ");
            Files.writeString(store.resolve("D" + i + ".java"), source);
        }
        long total = files * bytesPerFile;
        assertTrue(total <= Limits.DEFAULT_BYTES && files <= Limits.DEFAULT_FILES && bytesPerFile <= Limits.DEFAULT_FILE_BYTES);
        CodeService service = Fixtures.service(store);
        AtomicLong live = new AtomicLong();
        Thread sampler = new Thread(() -> {
            while (!Thread.currentThread().isInterrupted()) {
                for (MemoryPoolMXBean pool : ManagementFactory.getMemoryPoolMXBeans()) {
                    if (pool.getType() == MemoryType.HEAP && pool.getCollectionUsage() != null) {
                        live.accumulateAndGet(pool.getCollectionUsage().getUsed(), Math::max); // after the last GC
                    }
                }
                try {
                    Thread.sleep(50);
                } catch (InterruptedException e) {
                    return;
                }
            }
        });
        sampler.setDaemon(true);
        sampler.start();
        long start = System.nanoTime();
        JsonNode output = Fixtures.validateExtract(service.extract(Fixtures.extractInput(store, null)));
        long ms = (System.nanoTime() - start) / 1_000_000;
        sampler.interrupt();
        long peak = live.get();
        System.out.printf(
                "BENCHMARK max-cap %s: %d files, %d bytes, %d elements, %d ms, heap in use after a GC at most %d MiB (max heap %d MiB)%n",
                label, files, total, output.get("elements").size(), ms, peak >> 20, Runtime.getRuntime().maxMemory() >> 20);
        assertTrue(output.get("elements").size() > files);
    }

    @Test
    void maxBytesInFilesOfThePerFileCap() throws Exception {
        run("big files", (int) (Limits.DEFAULT_BYTES / Limits.DEFAULT_FILE_BYTES) - 1, Limits.DEFAULT_FILE_BYTES - 1024);
    }

    @Test
    void maxFilesOfSmallFiles() throws Exception {
        run("many files", Limits.DEFAULT_FILES, Limits.DEFAULT_BYTES / Limits.DEFAULT_FILES - 64);
    }
}
