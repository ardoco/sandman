package edu.kit.kastel.mcse.ardoco.sandman.inconsistency;

import java.util.List;
import java.util.Map;
import java.util.Set;

/** Current Requirement without an active, non-rejected link to a current element. */
public final class StubUntracedDetector implements InconsistencyDetector {

    static final String QUERY = """
            MATCH (:Project {id: $p})-[:HAS_ARTIFACT]->(a:Artifact)-[:CURRENT_VERSION]->(:ArtifactVersion)
                  -[:HAS_ELEMENT]->(r:Requirement)
            WHERE $all OR a.id IN $artifacts
            OPTIONAL MATCH (r)<-[:SOURCE|TARGET]-(l:TraceLink {active: true})-[:SOURCE|TARGET]->(o:Element)
                  <-[:HAS_ELEMENT]-(:ArtifactVersion)<-[:CURRENT_VERSION]-(:Artifact)
            WHERE o <> r AND l.status <> 'rejected'
            WITH r, count(l) AS links
            WHERE links = 0
            RETURN r.key AS key, r.name AS name, r.text AS text
            ORDER BY key
            """;

    @Override
    public String id() {
        return "stub-untraced";
    }

    @Override
    public String version() {
        return "0.1.0";
    }

    @Override
    public Set<String> types() {
        return Set.of("untraced-requirement");
    }

    @Override
    public String description() {
        return "Requirements without an active, non-rejected trace link";
    }

    @Override
    public List<Finding> detect(DetectionScope scope, DetectorContext ctx) {
        var rows = ctx.kg()
                .read(
                        QUERY,
                        Map.of("p", scope.projectId(), "all", scope.wholeProject(), "artifacts", scope.artifactIds()));
        return rows.stream()
                .map(row -> {
                    String name = String.valueOf(row.get("name"));
                    String title = ("Requirement without trace link: " + name);
                    Map<String, Object> evidence = row.get("text") == null ? null : Map.of("text", row.get("text"));
                    return new Finding(
                            "untraced-requirement",
                            "warning",
                            title.substring(0, Math.min(200, title.length())),
                            "The requirement has no active trace link to a current element.",
                            null,
                            List.of(Finding.Involved.element((String) row.get("key"), "subject")),
                            evidence);
                })
                .toList();
    }
}
