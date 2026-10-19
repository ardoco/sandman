package edu.kit.kastel.mcse.ardoco.sandman.inconsistency;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import edu.kit.kastel.mcse.ardoco.core.api.models.ModelFormat;
import edu.kit.kastel.mcse.ardoco.sandman.agent.ArtifactStore;
import edu.kit.kastel.mcse.ardoco.sandman.agent.Contracts;
import edu.kit.kastel.mcse.ardoco.sandman.agent.Ids;
import edu.kit.kastel.mcse.ardoco.sandman.agent.KgClient;
import edu.kit.kastel.mcse.ardoco.sandman.agent.RunRecorder;
import edu.kit.kastel.mcse.ardoco.sandman.agent.Settings;
import edu.kit.kastel.mcse.ardoco.sandman.ardocotext.CorenlpProvider;
import edu.kit.kastel.mcse.ardoco.sandman.inconsistency.ardoco.StandaloneBaseline;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.TreeSet;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.condition.EnabledIf;
import org.junit.jupiter.api.io.TempDir;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

/**
 * T3.3 on the Quarkus classpath of the inconsistency-agent: the benchmark KG holds the links of the TLR approach
 * 'ardoco' (recorded goldens of approach-ardoco). The detector 'ardoco-id' with linkApproaches [ardoco] writes findings
 * that equal the standalone ArDoCo runner on the same raw text and model. MediaStore keeps two extra text findings
 * (sentences 9 and 37, word "download"): the TLR release links fewer sentence pairs than the ID branch (see T3.3).
 */
@EnabledIf("edu.kit.kastel.mcse.ardoco.sandman.inconsistency.Docker#available")
class TlrLinksEquivalenceKgTest {

    static final String PROJECT = "bench";
    static final Path FIXTURES = Path.of("../extraction-agent/tests/fixtures/benchmark");
    static final Path GOLDEN = Path.of("../extraction-agent/tests/golden/benchmark");
    static final JsonMapper JSON = JsonMapper.builder().build();

    static KgClient kg;
    static Contracts contracts;

    @TempDir
    Path tmp;

    @BeforeAll
    static void start() throws Exception {
        CorenlpProvider.start();
        kg = TestKg.client();
        contracts = new Contracts(Settings.defaultContractsDir());
    }

    @AfterAll
    static void close() {
        if (kg != null) {
            kg.close();
        }
    }

    @ParameterizedTest
    @CsvSource({
        "mediastore, mediastore.txt, pcm/ms.repository, pcm-repository, PCM, ms.repository, 9 download|37 download",
        "mediastore, mediastore.txt, uml/ms.uml, uml, UML, ms.uml, 9 download|37 download",
        "teastore, teastore.txt, pcm/teastore.repository, pcm-repository, PCM, teastore.repository, -",
        "teastore, teastore.txt, uml/teastore.uml, uml, UML, teastore.uml, -"
    })
    void detectorEqualsStandaloneRunner(
            String project,
            String text,
            String model,
            String artifactFormat,
            ModelFormat format,
            String name,
            String extra)
            throws Exception {
        TestKg.reset(kg, PROJECT);
        var store = new ArtifactStore(tmp.resolve("store"));
        Path rawText = FIXTURES.resolve(project).resolve(text);
        Path modelFile = FIXTURES.resolve(project).resolve(model);
        String docArt = TestKg.artifact(kg, PROJECT, text, "architecture-doc");
        String modelArt = TestKg.artifact(kg, PROJECT, name, "architecture-model");
        kg.write("MATCH (a:Artifact {id: $a}) SET a.format = $f", Map.of("a", modelArt, "f", artifactFormat));
        store(store, docArt, Files.readAllBytes(rawText));
        store(store, modelArt, Files.readAllBytes(modelFile));
        seedSentences(docArt, GOLDEN.resolve(project).resolve(text + ".json"));
        seedModel(modelArt, GOLDEN.resolve(project).resolve(model + ".json"));
        int links = seedLinks(project + "-" + name);
        assertTrue(links > 0);

        var expected = StandaloneBaseline.run(rawText, modelFile, format, tmp.resolve("standalone"));

        Path yaml = tmp.resolve("plugins.yaml");
        Files.writeString(yaml, "detectors:\n  ardoco-id: { enabled: true, params: { linkApproaches: [ardoco] } }\n");
        var service = new DetectionService(
                kg,
                contracts,
                new RunRecorder(kg, "inconsistency-agent", "test"),
                DetectorRegistry.load(yaml, contracts),
                "http://query-service:8000",
                n -> {},
                store);
        var result =
                service.detect(new DetectionService.Request(PROJECT, null, List.of("ardoco-id"), null, null, null));
        assertEquals("completed", result.output().get("status").asString());

        var modelIds = new TreeSet<String>();
        kg.read(
                        "MATCH (:Inconsistency {type: 'undocumented-model-element'})-[:INVOLVES]->(e) RETURN e.key AS k",
                        Map.of())
                .forEach(r -> modelIds.add(localKey((String) r.get("k"))));
        var found = new TreeSet<String>();
        kg.read(
                        "MATCH (i:Inconsistency {type: 'missing-model-element'})-[:INVOLVES]->(s:TextUnit) "
                                + "RETURN s.sentenceIndex AS n, i.evidence AS ev",
                        Map.of())
                .forEach(r -> {
                    for (JsonNode w : JSON.readTree((String) r.get("ev")).get("words")) {
                        found.add((((Number) r.get("n")).intValue() + 1) + " "
                                + w.get("word").asString());
                    }
                });
        assertFalse(modelIds.isEmpty());
        System.out.printf(
                "T33-KG %s/%s: %d links in KG, standalone %d model + %d text, detector %d model + %d text, run %s%n",
                project,
                model,
                links,
                expected.modelIds().size(),
                expected.textFindings().size(),
                modelIds.size(),
                found.size(),
                result.output().get("detectorRuns").get(0));
        assertEquals(new TreeSet<>(expected.modelIds()), modelIds, "undocumented-model-element findings");
        var more = new TreeSet<>(found);
        more.removeAll(expected.textFindings());
        assertEquals(
                "-".equals(extra) ? new TreeSet<String>() : new TreeSet<>(List.of(extra.split("\\|"))),
                more,
                "text findings beyond the standalone runner");
        assertTrue(found.containsAll(expected.textFindings()), "no standalone text finding is lost");
    }

    static String localKey(String key) {
        return key.substring(key.indexOf('#') + 1);
    }

    void store(ArtifactStore store, String artifactId, byte[] data) throws Exception {
        var stored = store.put(PROJECT, data);
        kg.write(
                "MATCH (v:ArtifactVersion {id: $v}) SET v.storageUri = $u",
                Map.of("v", TestKg.version(artifactId), "u", stored.uri()));
    }

    void seedSentences(String docArt, Path golden) throws Exception {
        String version = TestKg.version(docArt);
        List<Map<String, Object>> rows = new ArrayList<>();
        for (JsonNode e : JSON.readTree(Files.readString(golden)).get("elements")) {
            if ("sentence".equals(e.get("kind").asString())) {
                String text = e.get("text").asString();
                String key = e.get("localKey").asString();
                rows.add(Map.of(
                        "id", Ids.stableId("el", version, key),
                        "key", docArt + "#" + key,
                        "name", text.substring(0, Math.min(40, text.length())),
                        "text", text,
                        "i", e.get("sentenceIndex").asInt()));
            }
        }
        kg.write(
                "MATCH (v:ArtifactVersion {id: $v}) UNWIND $rows AS r CREATE (e:Element:TextUnit {id: r.id, key: r.key, "
                        + "projectId: $p, kind: 'sentence', name: r.name, text: r.text, sentenceIndex: r.i, "
                        + "contentHash: 'h', runId: 'run_x'}) CREATE (v)-[:HAS_ELEMENT]->(e)",
                Map.of("v", version, "p", PROJECT, "rows", rows));
    }

    void seedModel(String modelArt, Path golden) throws Exception {
        String version = TestKg.version(modelArt);
        List<Map<String, Object>> rows = new ArrayList<>();
        for (JsonNode e : JSON.readTree(Files.readString(golden)).get("elements")) {
            if (!"file".equals(e.get("kind").asString())) {
                String key = e.get("localKey").asString();
                rows.add(Map.of(
                        "id", Ids.stableId("el", version, key),
                        "key", modelArt + "#" + key,
                        "name", e.get("name").asString()));
            }
        }
        kg.write(
                "MATCH (v:ArtifactVersion {id: $v}) UNWIND $rows AS r CREATE (e:Element:ModelElement {id: r.id, key: r.key, "
                        + "projectId: $p, kind: 'x', name: r.name, contentHash: 'h', runId: 'run_x'}) "
                        + "CREATE (v)-[:HAS_ELEMENT]->(e)",
                Map.of("v", version, "p", PROJECT, "rows", rows));
    }

    /** Links of the recorded TLR goldens (1-based sentence, model id) as active TraceLinks of approach 'ardoco'. */
    int seedLinks(String csvName) throws Exception {
        List<Map<String, Object>> rows = new ArrayList<>();
        try (var in = getClass().getResourceAsStream("/tlr-ardoco-links/" + csvName + ".csv")) {
            for (String row : new String(in.readAllBytes()).lines().skip(1).toList()) {
                String[] p = row.split(",");
                rows.add(Map.of("m", p[0], "n", Integer.parseInt(p[1]) - 1));
            }
        }
        kg.write(
                "UNWIND $rows AS r MATCH (s:TextUnit {sentenceIndex: r.n}), (m:ModelElement) WHERE m.key ENDS WITH '#' + r.m "
                        + "CREATE (l:TraceLink {id: 'tl_' + s.id + m.id, projectId: $p, linkType: 'documents', confidence: 1.0, "
                        + "approach: 'ardoco', approachVersion: '0', configHash: 'c', status: 'proposed', active: true, "
                        + "runId: 'run_x'}) CREATE (l)-[:SOURCE]->(s), (l)-[:TARGET]->(m)",
                Map.of("p", PROJECT, "rows", rows));
        return rows.size();
    }
}
