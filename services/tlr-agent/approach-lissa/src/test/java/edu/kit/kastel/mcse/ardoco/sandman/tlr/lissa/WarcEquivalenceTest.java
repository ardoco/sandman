package edu.kit.kastel.mcse.ardoco.sandman.tlr.lissa;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assumptions.assumeTrue;

import edu.kit.kastel.mcse.ardoco.sandman.agent.ArtifactStore;
import edu.kit.kastel.mcse.ardoco.sandman.agent.Ids;
import edu.kit.kastel.mcse.ardoco.sandman.agent.LlmAccess;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.CandidateLink;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.TlrContext;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.TlrInput;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.lissa.utils.KeyGenerator;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.stream.Stream;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

/**
 * Equivalence with upstream LiSSA-RATLR on the WARC data set (commit {@link LissaApproach#LISSA_COMMIT}): the pinned
 * upstream caches answer the gateway, and the approach must reach the upstream result. The upstream data set links
 * requirements to requirements, so the target files enter as code files and the prompt label "source code" maps back
 * to "requirement" for the cache lookup. Run with
 * {@code mvn -Pbenchmark -pl services/tlr-agent/approach-lissa test}. Data: see docs/tasks/T2.2-adapted-lissa.md.
 */
@Tag("benchmark")
class WarcEquivalenceTest {

    private static final Logger LOG = LoggerFactory.getLogger(WarcEquivalenceTest.class);
    private static final Path DATA = Path.of(System.getProperty("user.home"), ".cache/sandman/lissa-warc");
    private static final String EMBED_CACHE = "cache/OpenAiEmbeddingCreator_text-embedding-3-large.json";
    private static final String CLASSIFIER_CACHE = "cache/SimpleClassifier_gpt-4o-mini-2024-07-18_133742243.json";
    private static final String EMBED_SHA = "b4f057a2b54fa6640b6d5aa67b95cb05c4cbcce5d9ec7ecb0d242fc3db131ff2";
    private static final String CLASSIFIER_SHA = "763ca90c20d5bd8a61c3c426c5ea836a4692774499b8061f0240024ec760697d";
    private static final String ANSWER_SHA = "086cd0544a6e87a519d1a900cb04c9f2f8e5c62ad17d29863a974d05cc0e1241";

    @TempDir
    Path root;

    @Test
    void reproducesUpstreamPrecisionRecallAndF1() throws IOException {
        assumeTrue(Files.isDirectory(DATA), "WARC data set missing in " + DATA);
        assertEquals(EMBED_SHA, sha(DATA.resolve(EMBED_CACHE)));
        assertEquals(CLASSIFIER_SHA, sha(DATA.resolve(CLASSIFIER_CACHE)));
        assertEquals(ANSWER_SHA, sha(DATA.resolve("answer.csv")));

        JsonNode embeddings = JsonMapper.shared().readTree(Files.readString(DATA.resolve(EMBED_CACHE)));
        JsonNode answers = JsonMapper.shared().readTree(Files.readString(DATA.resolve(CLASSIFIER_CACHE)));

        ArtifactStore store = new ArtifactStore(root);
        List<TlrInput.Version> versions = new ArrayList<>();
        List<TlrInput.Element> targets = new ArrayList<>();
        for (Path file : list(DATA.resolve("low"))) {
            String name = file.getFileName().toString();
            var stored = store.put("warc", Files.readAllBytes(file));
            versions.add(new TlrInput.Version("v-" + name, "a-" + name, name, "code", null, stored.uri()));
            targets.add(
                    new TlrInput.Element(name, "v-" + name, name, name, "CodeUnit", "file", name, null, null, "code"));
        }
        List<TlrInput.Element> sources = new ArrayList<>();
        for (Path file : list(DATA.resolve("high"))) {
            String name = file.getFileName().toString();
            sources.add(new TlrInput.Element(
                    name,
                    "vr",
                    name,
                    name,
                    "Requirement",
                    "requirement",
                    name,
                    Files.readString(file, StandardCharsets.UTF_8),
                    0,
                    "requirements"));
        }

        List<CandidateLink> links;
        try (FakeGateway gateway = new FakeGateway()) {
            gateway.embedAnswer = body -> vector(embeddings, body.get("input").asString());
            gateway.chatAnswer = body -> {
                String prompt = body.get("messages").get(0).get("content").asString();
                String upstreamPrompt = prompt.replace("source code: '''", "requirement: '''");
                JsonNode answer = answers.get(KeyGenerator.generateKey(upstreamPrompt));
                if (answer == null) {
                    throw new IllegalStateException("classifier cache miss: " + upstreamPrompt);
                }
                return answer.asString();
            };
            LlmAccess access = new LlmAccess(
                    "http://127.0.0.1", "key", Map.of("chat-default", "gpt", "embed-default", "emb"), false);
            JsonNode params = JsonMapper.shared().readTree("{\"retrieval\":{\"k\":4}}");
            links = new LissaApproach()
                    .recover(
                            new TlrInput("warc", versions, sources, targets),
                            new TlrContext(access, store, () -> false, LOG, params, gateway.client()));
        }

        Set<String> found = new HashSet<>();
        links.forEach(l -> found.add(l.sourceId() + "," + l.targetId()));
        Set<String> gold = new HashSet<>();
        Files.readAllLines(DATA.resolve("answer.csv")).stream()
                .skip(1)
                .filter(l -> !l.isBlank())
                .forEach(gold::add);
        long truePositives = found.stream().filter(gold::contains).count();
        double precision = (double) truePositives / found.size();
        double recall = (double) truePositives / gold.size();
        double f1 = 2 * precision * recall / (precision + recall);

        assertEquals(0.38, precision, 1e-12);
        assertEquals(0.6985294117647058, recall, 1e-12);
        assertEquals(0.49222797927461137, f1, 1e-12);
    }

    private static float[] vector(JsonNode embeddings, String input) {
        JsonNode entry = embeddings.get(KeyGenerator.generateKey(input));
        if (entry == null) {
            throw new IllegalStateException("embedding cache miss: " + input);
        }
        JsonNode array = JsonMapper.shared().readTree(entry.asString());
        float[] vector = new float[array.size()];
        for (int i = 0; i < vector.length; i++) {
            vector[i] = array.get(i).floatValue();
        }
        return vector;
    }

    private static List<Path> list(Path dir) throws IOException {
        try (Stream<Path> files = Files.list(dir)) {
            return files.sorted().toList();
        }
    }

    private static String sha(Path file) throws IOException {
        return Ids.sha256Hex(Files.readAllBytes(file));
    }
}
