package edu.kit.kastel.mcse.ardoco.sandman.tlr;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import edu.kit.kastel.mcse.ardoco.sandman.agent.ArtifactStore;
import edu.kit.kastel.mcse.ardoco.sandman.agent.Ids;
import edu.kit.kastel.mcse.ardoco.sandman.agent.KgClient;
import edu.kit.kastel.mcse.ardoco.sandman.agent.a2a.A2aClient;
import edu.kit.kastel.mcse.ardoco.sandman.ardocotext.CorenlpProvider;
import io.quarkus.test.common.http.TestHTTPResource;
import io.quarkus.test.junit.QuarkusTest;
import jakarta.inject.Inject;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeSet;
import java.util.UUID;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIf;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

/** T2.1 acceptance: the whole-project approach ardoco gives the same current links incrementally as in one batch run. */
@EnabledIf("dockerAvailable")
@QuarkusTest
class ArdocoIncrementalTest {

    static final String PROJECT = "ms";
    static final String APPROACH = "ardoco";
    static final Path FIXTURE = Path.of("../extraction-agent/tests/fixtures/benchmark/mediastore");
    static final Path GOLDEN = Path.of("../extraction-agent/tests/golden/benchmark/mediastore");
    /** A component that no other model element needs: removed in the second PCM version. */
    static final String REMOVED_COMPONENT = "Cache";

    static boolean dockerAvailable() {
        return RecoverTraceLinksTest.dockerAvailable();
    }

    @TestHTTPResource("/")
    URI url;

    @BeforeAll
    static void startProvider() throws Exception {
        CorenlpProvider.start();
    }

    @Inject
    KgClient kg;

    @Inject
    TestBeans.Events events;

    final List<A2aClient> clients = new ArrayList<>();

    @BeforeEach
    void reset() {
        events.events.clear();
        kg.write("MATCH (n) DETACH DELETE n", Map.of());
        kg.write("CREATE (:Project {id: $p, llmPolicy: 'cloud-allowed'})", Map.of("p", PROJECT));
    }

    @AfterEach
    void close() {
        clients.forEach(A2aClient::close);
    }

    // --- seeding

    record Seeded(String artifactId, String versionId) {}

    private JsonNode golden(String file) throws Exception {
        return JsonMapper.shared().readTree(Files.readString(GOLDEN.resolve(file)));
    }

    /** Sentences 0..count-1 of the MediaStore text as the extraction-agent would store them. */
    Seeded docVersion(String versionName, int count) throws Exception {
        List<Map<String, Object>> elements = new ArrayList<>();
        String artifactId = Ids.stableId("art", "doc");
        String versionId = Ids.stableId("ver", versionName);
        for (JsonNode e : golden("mediastore.txt.json").get("elements")) {
            if ("sentence".equals(e.get("kind").asString())
                    && e.get("sentenceIndex").asInt() < count) {
                elements.add(element(artifactId, versionId, e, "TextUnit"));
            }
        }
        assertEquals(count, elements.size());
        seed(artifactId, "architecture-doc", "mediastore.txt", "markdown", versionId, null, elements);
        return new Seeded(artifactId, versionId);
    }

    /** The model file and its elements; the component named without is left out of file and elements. */
    Seeded modelVersion(String path, String format, String versionName, String without) throws Exception {
        String artifactId = Ids.stableId("art", path);
        String versionId = Ids.stableId("ver", versionName);
        String content = Files.readString(FIXTURE.resolve(path));
        List<Map<String, Object>> elements = new ArrayList<>();
        for (JsonNode e : golden(path + ".json").get("elements")) {
            if ("file".equals(e.get("kind").asString())) {
                continue;
            }
            if (without != null && without.equals(e.get("name").asString())) {
                content = removeComponent(content, e.get("localKey").asString());
                continue;
            }
            elements.add(element(artifactId, versionId, e, "ModelElement"));
        }
        var stored = new ArtifactStore(TestBeans.ARTIFACTS).put(PROJECT, content.getBytes(StandardCharsets.UTF_8));
        seed(artifactId, "architecture-model", path, format, versionId, stored.uri(), elements);
        return new Seeded(artifactId, versionId);
    }

    /** PCM only: drops the components__Repository block with the id (these blocks do not nest). */
    static String removeComponent(String xml, String id) {
        int start = xml.indexOf("<components__Repository xsi:type=\"repository:BasicComponent\" id=\"" + id + "\"");
        assertTrue(start >= 0, "component " + id + " not found in model file");
        String end = "</components__Repository>";
        return xml.substring(0, xml.lastIndexOf('\n', start) + 1)
                + xml.substring(xml.indexOf('\n', xml.indexOf(end, start)) + 1);
    }

    private static Map<String, Object> element(String artifactId, String versionId, JsonNode e, String label) {
        String localKey = e.get("localKey").asString();
        Map<String, Object> m = new java.util.HashMap<>();
        m.put("id", Ids.stableId("el", versionId, localKey));
        m.put("key", artifactId + "#" + localKey);
        m.put("label", label);
        m.put("kind", e.get("kind").asString());
        m.put("name", e.get("name").asString());
        m.put("text", e.has("text") && !e.get("text").isNull() ? e.get("text").asString() : null);
        m.put(
                "sentenceIndex",
                e.has("sentenceIndex") && !e.get("sentenceIndex").isNull()
                        ? e.get("sentenceIndex").asInt()
                        : null);
        return m;
    }

    /** Creates the version and makes it the artifact's current one. */
    private void seed(
            String artifactId,
            String kind,
            String path,
            String format,
            String versionId,
            String storageUri,
            List<Map<String, Object>> elements) {
        Map<String, Object> params = new java.util.HashMap<>();
        params.put("p", PROJECT);
        params.put("a", artifactId);
        params.put("kind", kind);
        params.put("path", path);
        params.put("format", format);
        params.put("v", versionId);
        params.put("uri", storageUri);
        kg.write(
                "MERGE (a:Artifact {id: $a}) SET a.projectId = $p, a.kind = $kind, a.path = $path, a.format = $format "
                        + "WITH a OPTIONAL MATCH (a)-[c:CURRENT_VERSION]->() DELETE c "
                        + "WITH DISTINCT a CREATE (a)-[:HAS_VERSION]->(v:ArtifactVersion {id: $v, projectId: $p, storageUri: $uri}) "
                        + "CREATE (a)-[:CURRENT_VERSION]->(v)",
                params);
        for (Map<String, Object> e : elements) {
            Map<String, Object> q = new java.util.HashMap<>(e);
            q.put("v", versionId);
            q.put("p", PROJECT);
            String label = (String) e.get("label");
            kg.write(
                    "MATCH (v:ArtifactVersion {id: $v}) CREATE (v)-[:HAS_ELEMENT]->(x:Element:" + label
                            + " {id: $id, projectId: $p, key: $key, kind: $kind, name: $name, text: $text, sentenceIndex: $sentenceIndex})",
                    q);
        }
    }

    // --- running

    JsonNode input(List<Seeded> sources, List<Seeded> targets) {
        var node = JsonMapper.shared().createObjectNode().put("projectId", PROJECT);
        sources.forEach(s -> node.withArray("sourceVersionIds").add(s.versionId()));
        targets.forEach(s -> node.withArray("targetVersionIds").add(s.versionId()));
        node.withArray("approaches").add(APPROACH);
        return node;
    }

    A2aClient.TaskSnapshot run(JsonNode input) throws InterruptedException {
        A2aClient client = new A2aClient(TestBeans.TOKENS.get("orchestrator"));
        clients.add(client);
        var task = client.submit(
                url.toString(),
                "recover-trace-links",
                input,
                null,
                null,
                UUID.randomUUID().toString());
        for (int i = 0; i < 12_000 && !task.terminal(); i++) {
            Thread.sleep(10);
            task = client.get(url.toString(), task.taskId());
        }
        assertTrue(task.terminal(), "task did not finish: " + task);
        assertEquals("TASK_STATE_COMPLETED", task.state(), String.valueOf(task.error()));
        assertEquals("completed", task.result().at("/runs/0/status").asString(), String.valueOf(task.result()));
        return task;
    }

    Set<String> activeIds() {
        return new TreeSet<>(
                kg.read("MATCH (l:TraceLink {approach: 'ardoco', active: true}) RETURN l.id AS id", Map.of()).stream()
                        .map(r -> (String) r.get("id"))
                        .toList());
    }

    long activeLinksOutsideCurrent() {
        return (Long) kg.read(
                        "MATCH (l:TraceLink {approach: 'ardoco', active: true})-[:SOURCE|TARGET]->(:Element)"
                                + "<-[:HAS_ELEMENT]-(v:ArtifactVersion)<-[:HAS_VERSION]-(a:Artifact) "
                                + "WHERE NOT (a)-[:CURRENT_VERSION]->(v) RETURN count(*) AS n",
                        Map.of())
                .get(0)
                .get("n");
    }

    // --- tests

    @Test
    void incrementalRunsEqualOneBatchRun() throws Exception {
        var doc1 = docVersion("doc1", 30);
        var pcm1 = modelVersion("pcm/ms.repository", "pcm-repository", "pcm1", null);
        run(input(List.of(doc1), List.of(pcm1)));
        var afterFirst = activeIds();
        assertFalse(afterFirst.isEmpty());
        assertEquals(0, activeLinksOutsideCurrent());

        var doc2 = docVersion("doc2", 37);
        run(input(List.of(doc2), List.of(pcm1)));
        assertTrue(activeIds().size() > afterFirst.size());
        assertEquals(0, activeLinksOutsideCurrent());

        var uml = modelVersion("uml/ms.uml", "uml", "uml1", null);
        run(input(List.of(doc2), List.of(uml)));
        assertEquals(0, activeLinksOutsideCurrent());
        var withUml = activeIds();

        var pcm2 = modelVersion("pcm/ms.repository", "pcm-repository", "pcm2", REMOVED_COMPONENT);
        run(input(List.of(doc2), List.of(pcm2)));
        assertEquals(0, activeLinksOutsideCurrent());

        var merged = input(List.of(doc1, doc2), List.of(pcm1, pcm2, uml));
        var done = run(merged);
        assertEquals(0, activeLinksOutsideCurrent());
        var incremental = activeIds();
        assertFalse(incremental.equals(withUml));

        var first = kg.read(
                "MATCH (r:Run {tool: 'ardoco', a2aTaskId: $t}) RETURN r.configHash AS hash, r.toolVersion AS v",
                Map.of("t", done.taskId()));
        assertEquals("2.1.0+a2", first.get(0).get("v"));
        // determinism: the same input again gives the same ids and configHash
        var again = run(merged);
        assertEquals(incremental, activeIds());
        assertEquals(
                first.get(0).get("hash"),
                kg.read(
                                "MATCH (r:Run {tool: 'ardoco', a2aTaskId: $t}) RETURN r.configHash AS hash",
                                Map.of("t", again.taskId()))
                        .get(0)
                        .get("hash"));

        // batch: fresh KG with the final state only
        reset();
        var bDoc = docVersion("doc2", 37);
        var bPcm = modelVersion("pcm/ms.repository", "pcm-repository", "pcm2", REMOVED_COMPONENT);
        var bUml = modelVersion("uml/ms.uml", "uml", "uml1", null);
        run(input(List.of(bDoc), List.of(bPcm, bUml)));
        assertEquals(incremental, activeIds());
        assertEquals(0, activeLinksOutsideCurrent());
    }
}
