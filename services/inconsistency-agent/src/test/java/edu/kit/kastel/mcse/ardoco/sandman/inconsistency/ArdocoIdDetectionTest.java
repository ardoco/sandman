package edu.kit.kastel.mcse.ardoco.sandman.inconsistency;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import edu.kit.kastel.mcse.ardoco.sandman.agent.ArtifactStore;
import edu.kit.kastel.mcse.ardoco.sandman.agent.Contracts;
import edu.kit.kastel.mcse.ardoco.sandman.agent.Ids;
import edu.kit.kastel.mcse.ardoco.sandman.agent.KgClient;
import edu.kit.kastel.mcse.ardoco.sandman.agent.RunRecorder;
import edu.kit.kastel.mcse.ardoco.sandman.agent.Settings;
import edu.kit.kastel.mcse.ardoco.sandman.ardocotext.CorenlpProvider;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIf;
import org.junit.jupiter.api.io.TempDir;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

/**
 * The ardoco-id detector through DetectionService on the MediaStore benchmark (PCM): KG seeded from the extraction
 * goldens, CoreNLP from the provider container, ArDoCo links replaced by KG links of approach 'ardoco'.
 */
@EnabledIf("edu.kit.kastel.mcse.ardoco.sandman.inconsistency.Docker#available")
class ArdocoIdDetectionTest {

    static final String PROJECT = "ms";
    static final Path FIXTURE = Path.of("../extraction-agent/tests/fixtures/benchmark/mediastore");
    static final Path GOLDEN = Path.of("../extraction-agent/tests/golden/benchmark/mediastore");
    static final JsonMapper JSON = JsonMapper.builder().build();

    static KgClient kg;
    static Contracts contracts;
    static String provider;

    @TempDir
    Path tmp;

    String docArt;
    String modelArt;
    ArtifactStore store;
    List<String> modelLines;
    JsonNode modelGolden;

    @BeforeAll
    static void start() throws Exception {
        provider = CorenlpProvider.start();
        kg = TestKg.client();
        contracts = new Contracts(Settings.defaultContractsDir());
    }

    @AfterAll
    static void close() {
        if (kg != null) {
            kg.close();
        }
    }

    @BeforeEach
    void seed() throws Exception {
        TestKg.reset(kg, PROJECT);
        store = new ArtifactStore(tmp.resolve("store"));
        docArt = TestKg.artifact(kg, PROJECT, "mediastore.txt", "architecture-doc");
        modelArt = TestKg.artifact(kg, PROJECT, "ms.repository", "architecture-model");
        kg.write("MATCH (a:Artifact {id: $a}) SET a.format = 'pcm-repository'", Map.of("a", modelArt));
        store(docArt, Files.readAllBytes(FIXTURE.resolve("mediastore.txt")));
        modelLines = Files.readAllLines(FIXTURE.resolve("pcm/ms.repository"));
        store(modelArt, Files.readAllBytes(FIXTURE.resolve("pcm/ms.repository")));
        modelGolden = JSON.readTree(Files.readString(GOLDEN.resolve("pcm/ms.repository.json")));
        JsonNode docGolden = JSON.readTree(Files.readString(GOLDEN.resolve("mediastore.txt.json")));
        for (JsonNode e : docGolden.get("elements")) {
            if ("sentence".equals(e.get("kind").asString())) {
                sentence(
                        e.get("localKey").asString(),
                        e.get("text").asString(),
                        e.get("sentenceIndex").asInt());
            }
        }
        for (JsonNode e : modelGolden.get("elements")) {
            if (!"file".equals(e.get("kind").asString())) {
                TestKg.element(
                                kg,
                                PROJECT,
                                modelArt,
                                "ModelElement",
                                e.get("localKey").asString(),
                                "h")
                        .toString();
                kg.write(
                        "MATCH (e:ModelElement {key: $k}) SET e.name = $n",
                        Map.of(
                                "k",
                                modelArt + "#" + e.get("localKey").asString(),
                                "n",
                                e.get("name").asString()));
            }
        }
    }

    void store(String artifactId, byte[] data) throws Exception {
        var stored = store.put(PROJECT, data);
        kg.write(
                "MATCH (v:ArtifactVersion {id: $v}) SET v.storageUri = $u",
                Map.of("v", TestKg.version(artifactId), "u", stored.uri()));
    }

    void sentence(String localKey, String text, int index) {
        String version = TestKg.version(docArt);
        kg.write(
                "MATCH (v:ArtifactVersion {id: $v}) CREATE (e:Element:TextUnit {id: $id, key: $key, projectId: $p, "
                        + "kind: 'sentence', name: $name, text: $text, sentenceIndex: $i, contentHash: 'h', runId: 'run_x'}) "
                        + "CREATE (v)-[:HAS_ELEMENT]->(e)",
                Map.of(
                        "v", version,
                        "id", Ids.stableId("el", version, localKey),
                        "key", docArt + "#" + localKey,
                        "p", PROJECT,
                        "name", text.substring(0, Math.min(40, text.length())),
                        "text", text,
                        "i", index));
    }

    DetectionService service(String paramsYaml) throws Exception {
        Path yaml = tmp.resolve("plugins-" + System.nanoTime() + ".yaml");
        Files.writeString(yaml, "detectors:\n  ardoco-id: { enabled: true" + paramsYaml + " }\n");
        var registry = DetectorRegistry.load(yaml, contracts);
        return new DetectionService(
                kg,
                contracts,
                new RunRecorder(kg, "inconsistency-agent", "test"),
                registry,
                "http://query-service:8000",
                n -> {},
                store);
    }

    DetectionService.Result detect(DetectionService service) throws Exception {
        return service.detect(new DetectionService.Request(PROJECT, null, List.of("ardoco-id"), null, null, null));
    }

    Map<String, String> items() {
        Map<String, String> out = new TreeMap<>();
        kg.read("MATCH (i:Inconsistency) RETURN i.id AS id, i.type AS type, i.status AS s, i.title AS t", Map.of())
                .forEach(r -> out.put((String) r.get("id"), r.get("type") + "|" + r.get("s") + "|" + r.get("t")));
        return out;
    }

    @Test
    void reportsUndocumentedAndMissingFindings() throws Exception {
        detect(service(", params: { linkApproaches: [] }"));
        var items = items();
        assertFalse(items.isEmpty());
        assertTrue(items.values().stream().anyMatch(v -> v.startsWith("undocumented-model-element|OPEN|")));
        assertTrue(items.values().stream().anyMatch(v -> v.startsWith("missing-model-element|OPEN|")));
    }

    @Test
    void secondRunIsDeterministic() throws Exception {
        var s = service(", params: { linkApproaches: [] }");
        detect(s);
        var first = items();
        detect(s);
        assertEquals(first, items());
    }

    String itemsWith(String type, String status, Map<String, String> all) {
        return all.values().stream()
                .filter(v -> v.startsWith(type + "|" + status + "|"))
                .map(v -> v.substring(v.lastIndexOf('|') + 1))
                .sorted()
                .toList()
                .toString();
    }

    void linkByName(String approach) {
        kg.write(
                "MATCH (s:TextUnit {kind: 'sentence'}), (m:ModelElement) WHERE s.text CONTAINS m.name "
                        + "CREATE (l:TraceLink {id: 'tl_' + s.id + m.id, projectId: $p, linkType: 'relates', confidence: 0.9, "
                        + "approach: $a, approachVersion: '0', configHash: 'c', status: 'proposed', active: true, runId: 'run_x'}) "
                        + "CREATE (l)-[:SOURCE]->(s), (l)-[:TARGET]->(m)",
                Map.of("p", PROJECT, "a", approach));
    }

    @Test
    void linkApproachesFilterSelectsTheLinks() throws Exception {
        linkByName("other");
        detect(service(", params: { linkApproaches: [ardoco] }"));
        var withoutMatchingLinks = items();
        long undocumented = withoutMatchingLinks.values().stream()
                .filter(v -> v.startsWith("undocumented-model-element|"))
                .count();
        long components = 0;
        for (JsonNode e : modelGolden.get("elements")) {
            components += "component".equals(e.get("kind").asString()) ? 1 : 0;
        }
        assertEquals(components, undocumented, "every component is undocumented (interfaces are not a default type)");
        TestKg.reset(kg, PROJECT);
        seed();
        linkByName("ardoco");
        detect(service(", params: { linkApproaches: [ardoco] }"));
        long withLinks = items().values().stream()
                .filter(v -> v.startsWith("undocumented-model-element|"))
                .count();
        assertTrue(withLinks < undocumented, "links of the selected approach document elements");
    }

    @Test
    void removedComponentResolvesItsItem() throws Exception {
        var s = service(", params: { linkApproaches: [] }");
        detect(s);
        assertTrue(
                itemsWith("undocumented-model-element", "OPEN", items()).contains("Undocumented model element: Cache"));
        List<String> kept = new ArrayList<>(modelLines.subList(0, 533));
        kept.addAll(modelLines.subList(646, modelLines.size()));
        store(modelArt, (String.join("\n", kept) + "\n").getBytes(StandardCharsets.UTF_8));
        kg.write("MATCH (e:ModelElement {name: 'Cache'}) DETACH DELETE e", Map.of());
        var r = detect(s);
        assertTrue(r.output().get("autoResolvedIds").size() >= 1);
        assertTrue(itemsWith("undocumented-model-element", "RESOLVED", items())
                .contains("Undocumented model element: Cache"));
    }

    @Test
    void sentenceAddedAtTheStartKeepsStatuses() throws Exception {
        var s = service(", params: { linkApproaches: [] }");
        detect(s);
        var before = items();
        kg.write("MATCH (e:TextUnit {kind: 'sentence'}) SET e.sentenceIndex = e.sentenceIndex + 1", Map.of());
        String text = "The system stores media files for registered users.";
        sentence("sec:/" + edu.kit.kastel.mcse.ardoco.sandman.ardocotext.SentenceRendering.hash(text), text, 0);
        detect(s);
        var after = items();
        before.forEach((id, v) -> assertEquals(v, after.get(id), "item " + v));
    }

    @Test
    void everyFindingInvolvesACurrentElementAndHasItsOwnFingerprint() throws Exception {
        detect(service(", params: { linkApproaches: [] }"));
        var rows = kg.read(
                "MATCH (i:Inconsistency) "
                        + "OPTIONAL MATCH (i)-[:INVOLVES]->(e:Element)<-[:HAS_ELEMENT]-(:ArtifactVersion)"
                        + "<-[:CURRENT_VERSION]-(:Artifact) "
                        + "RETURN i.id AS id, i.fingerprint AS fp, count(e) AS involved",
                Map.of());
        assertFalse(rows.isEmpty());
        var fingerprints = new java.util.HashSet<Object>();
        for (var r : rows) {
            assertTrue(((Number) r.get("involved")).intValue() >= 1, "finding without a current involved element");
            assertTrue(fingerprints.add(r.get("fp")), "duplicate fingerprint " + r.get("fp"));
        }
        assertEquals(items().size(), fingerprints.size());
    }

    @Test
    void documentationOnlyChangeAutoResolvesTheFinding() throws Exception {
        var s = service(", params: { linkApproaches: [ardoco] }");
        detect(s);
        var before = itemsWith("undocumented-model-element", "OPEN", items());
        String[] added = {
            "The Packaging component packs the media files of a download.",
            "The Packaging component is called by the Facade component.",
            "The Reencoding component converts the audio files of a download.",
            "The Reencoding component is called by the Facade component."
        };
        for (int i = 0; i < added.length; i++) {
            sentence(
                    "sec:/" + edu.kit.kastel.mcse.ardoco.sandman.ardocotext.SentenceRendering.hash(added[i]),
                    added[i],
                    1000 + i);
        }
        linkByName("ardoco");
        var r = detect(s);
        var all = items();
        assertTrue(r.output().get("autoResolvedIds").size() >= 1);
        assertTrue(before.contains("Undocumented model element: Packaging"));
        assertTrue(itemsWith("undocumented-model-element", "RESOLVED", all)
                .contains("Undocumented model element: Packaging"));
    }

    @Test
    void coreNlpOutageFailsTheDetectorWithoutResolvingAnything() throws Exception {
        var s = service(", params: { linkApproaches: [] }");
        detect(s);
        var before = items();
        String saved = System.getProperty("SANDMAN_CORENLP_URL");
        System.setProperty("SANDMAN_CORENLP_URL", "http://127.0.0.1:9");
        try {
            var r = detect(s);
            assertEquals("partial", r.output().get("status").asString());
            assertEquals(
                    "failed",
                    r.output().get("detectorRuns").get(0).get("status").asString());
        } finally {
            System.setProperty("SANDMAN_CORENLP_URL", saved);
        }
        assertEquals(before, items());
    }
}
