package edu.kit.kastel.mcse.ardoco.sandman.tlr;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import edu.kit.kastel.mcse.ardoco.sandman.agent.ArtifactStore;
import edu.kit.kastel.mcse.ardoco.sandman.agent.Ids;
import edu.kit.kastel.mcse.ardoco.sandman.agent.KgClient;
import edu.kit.kastel.mcse.ardoco.sandman.agent.a2a.A2aClient;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.ardoco.CodeModels;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.ardoco.CorenlpProvider;
import io.quarkus.test.common.http.TestHTTPResource;
import io.quarkus.test.junit.QuarkusTest;
import jakarta.inject.Inject;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeSet;
import java.util.UUID;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIf;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

/**
 * T2.1b acceptance: the whole-project approach ardocode gives the same current links incrementally as in one batch run
 * (TeaStore, with and without the image service). The code model comes from the stand-in skill over real A2A.
 */
@EnabledIf("dockerAvailable")
@QuarkusTest
class ArdocodeIncrementalTest {

    static final String PROJECT = "ts";
    static final String APPROACH = "ardocode";
    static final Path GOLDEN = Path.of("../extraction-agent/tests/golden/benchmark/teastore/teastore.txt.json");
    static final String IMAGE_SERVICE = "services/tools.descartes.teastore.image/";

    static boolean dockerAvailable() {
        return RecoverTraceLinksTest.dockerAvailable();
    }

    @TestHTTPResource("/")
    URI url;

    @BeforeAll
    static void startProvider() throws Exception {
        CorenlpProvider.start();
        Path token = Files.createTempFile("code-model-token", ".txt");
        Files.writeString(token, TestBeans.TOKENS.get("orchestrator"));
        System.setProperty("SANDMAN_CODE_MODEL_TOKEN_FILE", token.toString());
    }

    @BeforeEach
    void pointAtThisService() {
        System.setProperty("SANDMAN_CODE_MODEL_URL", url.toString());
    }

    @AfterAll
    static void clearProperties() {
        System.clearProperty("SANDMAN_CODE_MODEL_URL");
        System.clearProperty("SANDMAN_CODE_MODEL_TOKEN_FILE");
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
        CodeModelStubSkill.acm = null;
    }

    // --- seeding

    record Seeded(String artifactId, String versionId) {}

    /** Sentences 0..count-1 of the TeaStore text as the extraction-agent would store them. */
    Seeded docVersion(String versionName, int count) throws Exception {
        List<Map<String, Object>> elements = new ArrayList<>();
        String artifactId = Ids.stableId("art", "doc");
        String versionId = Ids.stableId("ver", versionName);
        for (JsonNode e : JsonMapper.shared().readTree(Files.readString(GOLDEN)).get("elements")) {
            if ("sentence".equals(e.get("kind").asString())
                    && e.get("sentenceIndex").asInt() < count) {
                String localKey = e.get("localKey").asString();
                Map<String, Object> m = new HashMap<>();
                m.put("id", Ids.stableId("el", versionId, localKey));
                m.put("key", artifactId + "#" + localKey);
                m.put("label", "TextUnit");
                m.put("kind", "sentence");
                m.put("name", e.get("name").asString());
                m.put("text", e.get("text").asString());
                m.put("sentenceIndex", e.get("sentenceIndex").asInt());
                elements.add(m);
            }
        }
        assertEquals(count, elements.size());
        seed(artifactId, "architecture-doc", "teastore.txt", "markdown", versionId, null, elements);
        return new Seeded(artifactId, versionId);
    }

    /** One current code artifact per path, with its file element; the storage object is never read by the stub. */
    List<Seeded> codeVersions(String versionName, Set<String> paths) throws Exception {
        String uri = new ArtifactStore(TestBeans.ARTIFACTS)
                .put(PROJECT, "x".getBytes(StandardCharsets.UTF_8))
                .uri();
        List<Seeded> seeded = new ArrayList<>();
        for (String path : paths) {
            String artifactId = Ids.stableId("art", path);
            String versionId = Ids.stableId("ver", versionName, path);
            Map<String, Object> m = new HashMap<>();
            m.put("id", Ids.stableId("el", versionId, path));
            m.put("key", artifactId + "#" + path);
            m.put("label", "CodeUnit");
            m.put("kind", "file");
            m.put("name", path);
            m.put("text", null);
            m.put("sentenceIndex", null);
            seed(artifactId, "code", path, null, versionId, uri, List.of(m));
            seeded.add(new Seeded(artifactId, versionId));
        }
        return seeded;
    }

    /** The artifacts are no longer current (deleted from the project). */
    void remove(List<Seeded> versions) {
        for (Seeded s : versions) {
            kg.write("MATCH (a:Artifact {id: $a})-[c:CURRENT_VERSION]->() DELETE c", Map.of("a", s.artifactId()));
        }
    }

    private void seed(
            String artifactId,
            String kind,
            String path,
            String format,
            String versionId,
            String storageUri,
            List<Map<String, Object>> elements) {
        Map<String, Object> params = new HashMap<>();
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
            Map<String, Object> q = new HashMap<>(e);
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

    A2aClient.TaskSnapshot run(JsonNode input, String acm) throws InterruptedException {
        CodeModelStubSkill.acm = CodeModels.acm(acm);
        A2aClient client = new A2aClient(TestBeans.TOKENS.get("orchestrator"));
        clients.add(client);
        var task = client.submit(
                url.toString(),
                "recover-trace-links",
                input,
                null,
                null,
                UUID.randomUUID().toString());
        for (int i = 0; i < 30_000 && !task.terminal(); i++) {
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
                kg.read("MATCH (l:TraceLink {approach: 'ardocode', active: true}) RETURN l.id AS id", Map.of()).stream()
                        .map(r -> (String) r.get("id"))
                        .toList());
    }

    long activeLinksOutsideCurrent() {
        return (Long) kg.read(
                        "MATCH (l:TraceLink {approach: 'ardocode', active: true})-[:SOURCE|TARGET]->(:Element)"
                                + "<-[:HAS_ELEMENT]-(v:ArtifactVersion)<-[:HAS_VERSION]-(a:Artifact) "
                                + "WHERE NOT (a)-[:CURRENT_VERSION]->(v) RETURN count(*) AS n",
                        Map.of())
                .get(0)
                .get("n");
    }

    /** Active links whose target is a file element: ArDoCo links files only. */
    long activeLinksToFiles() {
        return (Long) kg.read(
                        "MATCH (l:TraceLink {approach: 'ardocode', active: true})-[:TARGET]->(t:CodeUnit {kind: 'file'}) "
                                + "RETURN count(*) AS n",
                        Map.of())
                .get(0)
                .get("n");
    }

    // --- tests

    @Test
    void incrementalRunsEqualOneBatchRun() throws Exception {
        Set<String> withoutImage =
                new TreeSet<>(CodeModels.compilationUnits(CodeModels.acm("teastore-code-model-noimage"))
                        .values());
        Set<String> full = new TreeSet<>(CodeModels.compilationUnits(CodeModels.acm("teastore-code-model"))
                .values());
        Set<String> imageOnly = new TreeSet<>(full);
        imageOnly.removeAll(withoutImage);
        assertFalse(imageOnly.isEmpty());
        assertTrue(imageOnly.stream().allMatch(p -> p.startsWith(IMAGE_SERVICE)));

        var doc1 = docVersion("doc1", 30);
        var code = codeVersions("code1", withoutImage);
        run(input(List.of(doc1), code), "teastore-code-model-noimage");
        var afterFirst = activeIds();
        assertFalse(afterFirst.isEmpty());
        assertEquals(0, activeLinksOutsideCurrent());
        assertEquals(afterFirst.size(), activeLinksToFiles());

        var doc2 = docVersion("doc2", 43);
        run(input(List.of(doc2), code), "teastore-code-model-noimage");
        assertTrue(activeIds().size() > afterFirst.size());
        assertEquals(0, activeLinksOutsideCurrent());

        var image = codeVersions("code2", imageOnly);
        var all = new ArrayList<>(code);
        all.addAll(image);
        run(input(List.of(doc2), all), "teastore-code-model");
        assertEquals(0, activeLinksOutsideCurrent());
        var withImage = activeIds();

        remove(image);
        run(input(List.of(doc2), code), "teastore-code-model-noimage");
        assertEquals(0, activeLinksOutsideCurrent());
        assertTrue(withImage.size() > activeIds().size());

        var merged = input(List.of(doc1, doc2), all);
        var done = run(merged, "teastore-code-model-noimage");
        assertEquals(0, activeLinksOutsideCurrent());
        var incremental = activeIds();

        var first = runRow(done.taskId());
        assertEquals("2.1.0+a1", first.get("v"));
        Set<String> current = new TreeSet<>(code.stream().map(Seeded::versionId).toList());
        current.add(doc2.versionId());
        assertEquals(current, used(done.taskId()), "USED: the current versions of the project, nothing superseded");
        // determinism: the same input again gives the same ids and configHash
        var again = run(merged, "teastore-code-model-noimage");
        assertEquals(incremental, activeIds());
        assertEquals(first.get("hash"), runRow(again.taskId()).get("hash"));

        // batch: fresh KG with the final state only
        reset();
        var bDoc = docVersion("doc2", 43);
        var bCode = codeVersions("code1", withoutImage);
        run(input(List.of(bDoc), bCode), "teastore-code-model-noimage");
        assertEquals(incremental, activeIds());
        assertEquals(0, activeLinksOutsideCurrent());
    }

    private Map<String, Object> runRow(String taskId) {
        return kg.read(
                        "MATCH (r:Run {tool: 'ardocode', a2aTaskId: $t}) RETURN r.configHash AS hash, r.toolVersion AS v",
                        Map.of("t", taskId))
                .get(0);
    }

    private Set<String> used(String taskId) {
        return kg
                .read(
                        "MATCH (r:Run {tool: 'ardocode', a2aTaskId: $t})-[:USED]->(v:ArtifactVersion) RETURN v.id AS id",
                        Map.of("t", taskId))
                .stream()
                .map(r -> (String) r.get("id"))
                .collect(java.util.stream.Collectors.toCollection(TreeSet::new));
    }
}
