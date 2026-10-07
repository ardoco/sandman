package edu.kit.kastel.mcse.ardoco.sandman.agent;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import io.nats.client.Connection;
import io.nats.client.JetStream;
import io.nats.client.Nats;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.stream.Collectors;
import org.junit.jupiter.api.Test;
import org.neo4j.driver.exceptions.Neo4jException;
import org.testcontainers.containers.GenericContainer;
import org.testcontainers.containers.wait.strategy.Wait;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;

/** Real Memgraph and NATS containers; skipped without Docker. */
@Testcontainers(disabledWithoutDocker = true)
class IntegrationTest {

    @Container
    static final GenericContainer<?> MEMGRAPH = new GenericContainer<>("memgraph/memgraph:3.13.1")
            .withCommand("--telemetry-enabled=false")
            .withExposedPorts(7687)
            .waitingFor(Wait.forListeningPort());

    @Container
    static final GenericContainer<?> NATS = new GenericContainer<>("nats:2.15.0-alpine")
            .withCommand("-js")
            .withExposedPorts(4222)
            .waitingFor(Wait.forLogMessage(".*Server is ready.*", 1));

    static KgClient kg() {
        return new KgClient("bolt://" + MEMGRAPH.getHost() + ":" + MEMGRAPH.getMappedPort(7687), null, null);
    }

    @Test
    void kgWriteThenReadAndReadTransactionRejectsWrites() {
        try (KgClient kg = kg()) {
            kg.write("CREATE (:Probe {id: $id})", Map.of("id", "p1"));
            assertEquals(
                    List.of(Map.of("id", "p1")),
                    kg.read("MATCH (p:Probe {id: $id}) RETURN p.id AS id", Map.of("id", "p1")));
            assertThrows(Neo4jException.class, () -> kg.read("CREATE (:Probe {id: 'p2'})", Map.of()));
            assertEquals(List.of(), kg.read("MATCH (p:Probe {id: 'p2'}) RETURN p", Map.of()));
        }
    }

    @Test
    void cleanupStaleRemovesOnlyFactsOfUnfinishedRuns() {
        try (KgClient kg = kg()) {
            var runs = new RunRecorder(kg, "test-agent", "0");
            String done = runs.start(RunRecorder.RunInfo.of("mini", "s", "t", "0", "k1"));
            kg.write(
                    "MATCH (r:Run {id: $r}) CREATE (r)-[:PRODUCED]->(:Element {id: 'keep', runId: $r})",
                    Map.of("r", done));
            runs.complete(done);
            String stale = runs.start(RunRecorder.RunInfo.of("mini", "s", "t", "0", "k1"));
            kg.write(
                    "MATCH (r:Run {id: $r}), (old:Element {id: 'keep'}) "
                            + "CREATE (r)-[:PRODUCED]->(:Element {id: 'drop', runId: $r}) CREATE (r)-[:PRODUCED]->(old)",
                    Map.of("r", stale));
            assertEquals(1, runs.cleanupStale("k1"));
            assertEquals(
                    List.of(Map.of("id", "keep")),
                    kg.read("MATCH (e:Element) WHERE e.id IN ['keep', 'drop'] RETURN e.id AS id", Map.of()));
            var status = kg
                    .read(
                            "MATCH (r:Run) WHERE r.id IN [$a, $b] RETURN r.id AS id, r.status AS s",
                            Map.of("a", done, "b", stale))
                    .stream()
                    .collect(Collectors.toMap(r -> r.get("id"), r -> r.get("s")));
            assertEquals(Map.of(done, "completed", stale, "failed"), status);
        }
    }

    @Test
    void initSchemaIsIdempotentAndMemoryLimitIsNotRetried() {
        try (KgClient kg = kg()) {
            var initFile = Settings.defaultContractsDir().resolve("kg/init.cypher");
            kg.initSchema(initFile);
            kg.initSchema(initFile);
            var info = kg.runAutoCommit("SHOW STORAGE INFO", Map.of());
            assertTrue(info.stream().anyMatch(r -> "memory_res".equals(r.get("storage info"))));
            long started = System.nanoTime();
            assertThrows(
                    KgClient.MemoryLimitException.class,
                    () -> kg.read(
                            "UNWIND range(1, 2000000) AS i WITH collect(i) AS l RETURN size(l) QUERY MEMORY LIMIT 1 MB",
                            Map.of()));
            assertTrue((System.nanoTime() - started) / 1_000_000_000L < 5, "memory-limit error must not be retried");
        }
    }

    @Test
    void publishConsumeRoundTripWithDeduplication() throws Exception {
        try (Connection nc = Nats.connect("nats://" + NATS.getHost() + ":" + NATS.getMappedPort(4222))) {
            Streams.ensure(nc.jetStreamManagement());
            Streams.ensure(nc.jetStreamManagement()); // idempotent
            JetStream js = nc.jetStream();
            var publisher = new EventPublisher(js, LibraryTest.CONTRACTS, "tlr-agent");
            Event sent = publisher.publish("sandman.tracelinks.updated", "mini", EventsTest.DATA, null, "m-1", null);
            publisher.publish("sandman.tracelinks.updated", "mini", EventsTest.DATA, null, "m-1", sent.id());

            List<Event> received = new CopyOnWriteArrayList<>();
            var stop = new AtomicBoolean();
            var consumer = new EventConsumer(js, "test-consumer");
            Thread thread = Thread.ofVirtual().start(() -> {
                try {
                    consumer.run(received::add, stop::get);
                } catch (Exception e) {
                    throw new IllegalStateException(e);
                }
            });
            for (int i = 0; i < 50 && received.isEmpty(); i++) {
                Thread.sleep(100);
            }
            Thread.sleep(500); // a duplicate would arrive meanwhile
            stop.set(true);
            thread.join();
            assertEquals(List.of(sent.id()), received.stream().map(Event::id).toList());
            assertEquals(
                    1,
                    nc.jetStreamManagement()
                            .getStreamInfo(Streams.STREAM)
                            .getStreamState()
                            .getMsgCount());
        }
    }
}
