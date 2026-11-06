package edu.kit.kastel.mcse.ardoco.sandman.inconsistency;

import edu.kit.kastel.mcse.ardoco.sandman.agent.Ids;
import edu.kit.kastel.mcse.ardoco.sandman.agent.KgClient;
import java.util.Map;
import org.testcontainers.containers.GenericContainer;
import org.testcontainers.containers.wait.strategy.Wait;

/** Shared Traverse container (started on first use) and fixture helpers. */
final class TestKg {

    static final GenericContainer<?> TRAVERSE = new GenericContainer<>(
                    "truespar/traverse@sha256:e39b5739a525f2b955dcac0e5747039845f3a5c3cd89397aec923312d5d69965")
            .withCreateContainerCmdModifier(cmd -> cmd.withPlatform("linux/amd64"))
            .withCommand("--data", "/data", "--server-agent", "Neo4j/5.26.0")
            .withExposedPorts(7690, 7691)
            .waitingFor(Wait.forHttp("/api/ready").forPort(7691));

    static {
        TRAVERSE.start();
    }

    private TestKg() {}

    static String uri() {
        return "bolt://" + TRAVERSE.getHost() + ":" + TRAVERSE.getMappedPort(7690);
    }

    static KgClient client() {
        return new KgClient(uri(), null, null);
    }

    static void reset(KgClient kg, String project) {
        kg.write("MATCH (n) DETACH DELETE n", Map.of());
        kg.write("CREATE (:Project {id: $p, llmPolicy: 'local-only'})", Map.of("p", project));
    }

    /** Artifact with one current version; returns the artifact id. */
    static String artifact(KgClient kg, String project, String path, String kind) {
        String art = Ids.stableId("art", project, path);
        kg.write(
                "MATCH (p:Project {id: $p}) CREATE (a:Artifact {id: $a, projectId: $p, path: $path, kind: $kind}), "
                        + "(v:ArtifactVersion {id: $v, artifactId: $a, projectId: $p}) "
                        + "CREATE (p)-[:HAS_ARTIFACT]->(a), (a)-[:HAS_VERSION]->(v), (a)-[:CURRENT_VERSION]->(v)",
                Map.of("p", project, "a", art, "v", version(art), "path", path, "kind", kind));
        return art;
    }

    static String version(String artifactId) {
        return Ids.stableId("ver", artifactId, "v1");
    }

    /** Current element of the artifact's version; returns its key. */
    static String element(KgClient kg, String project, String artifactId, String label, String localKey, String hash) {
        String key = artifactId + "#" + localKey;
        kg.write(
                "MATCH (v:ArtifactVersion {id: $v}) CREATE (e:Element:" + label
                        + " {id: $id, key: $key, projectId: $p, kind: 'x', name: $name, text: 'text of ' + $name, "
                        + "contentHash: $hash, runId: 'run_x'}) CREATE (v)-[:HAS_ELEMENT]->(e)",
                Map.of(
                        "v", version(artifactId),
                        "id", Ids.stableId("el", version(artifactId), localKey),
                        "key", key,
                        "p", project,
                        "name", localKey,
                        "hash", hash));
        return key;
    }

    static String link(KgClient kg, String project, String sourceKey, String targetKey, boolean active, String status) {
        String id = Ids.stableId("tl", sourceKey, targetKey);
        kg.write(
                "MATCH (s:Element {key: $s}), (t:Element {key: $t}) "
                        + "CREATE (l:TraceLink {id: $id, projectId: $p, linkType: 'relates', confidence: 0.9, approach: 'stub', "
                        + "approachVersion: '0', configHash: 'c', status: $status, active: $active, runId: 'run_x'}) "
                        + "CREATE (l)-[:SOURCE]->(s), (l)-[:TARGET]->(t)",
                Map.of("s", sourceKey, "t", targetKey, "id", id, "p", project, "status", status, "active", active));
        return id;
    }
}
