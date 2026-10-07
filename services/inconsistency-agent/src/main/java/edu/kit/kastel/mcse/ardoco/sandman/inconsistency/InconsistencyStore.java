package edu.kit.kastel.mcse.ardoco.sandman.inconsistency;

import edu.kit.kastel.mcse.ardoco.sandman.agent.KgClient;
import edu.kit.kastel.mcse.ardoco.sandman.inconsistency.Lifecycle.Status;
import java.util.ArrayList;
import java.util.Collection;
import java.util.HashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import org.neo4j.driver.types.Node;

/** Reads of Inconsistency nodes shared by detection and triage. */
final class InconsistencyStore {

    /** Node properties plus the involved keys (element key, or sourceKey->targetKey for a link). */
    record Item(Map<String, Object> props, List<String> keys) {
        String id() {
            return (String) props.get("id");
        }

        Status status() {
            return Status.valueOf((String) props.get("status"));
        }

        String verification() {
            return (String) props.get("verification");
        }

        Set<String> artifactIds() {
            return artifactIdsOf(keys);
        }
    }

    private InconsistencyStore() {}

    /** Artifact ids of keys 'artifactId#localKey', links as 'key->key'. */
    static Set<String> artifactIdsOf(Collection<String> keys) {
        Set<String> ids = new LinkedHashSet<>();
        for (String key : keys) {
            for (String part : key.split("->")) {
                ids.add(part.substring(0, part.indexOf('#')));
            }
        }
        return ids;
    }

    // ponytail: loads the whole project; push filters into Cypher when a project exceeds ~10^4 inconsistencies
    static List<Item> loadAll(KgClient kg, String projectId) {
        Map<String, List<String>> keys = new HashMap<>();
        for (var row : kg.read(
                "MATCH (i:Inconsistency {projectId: $p})-[:INVOLVES]->(x) "
                        + "OPTIONAL MATCH (x)-[:SOURCE]->(s:Element) OPTIONAL MATCH (x)-[:TARGET]->(t:Element) "
                        + "RETURN i.id AS id, x.key AS key, s.key AS sk, t.key AS tk",
                Map.of("p", projectId))) {
            String key = row.get("key") != null ? (String) row.get("key") : row.get("sk") + "->" + row.get("tk");
            keys.computeIfAbsent((String) row.get("id"), k -> new ArrayList<>()).add(key);
        }
        return kg.read("MATCH (i:Inconsistency {projectId: $p}) RETURN i", Map.of("p", projectId)).stream()
                .map(row -> ((Node) row.get("i")).asMap())
                .map(props -> new Item(props, keys.getOrDefault((String) props.get("id"), List.of())))
                .toList();
    }

    /** Artifact id to path. */
    static Map<String, String> artifactPaths(KgClient kg, Collection<String> artifactIds) {
        Map<String, String> paths = new HashMap<>();
        kg.read(
                        "MATCH (a:Artifact) WHERE a.id IN $ids RETURN a.id AS id, a.path AS path",
                        Map.of("ids", List.copyOf(artifactIds)))
                .forEach(row -> paths.put((String) row.get("id"), (String) row.get("path")));
        return paths;
    }
}
