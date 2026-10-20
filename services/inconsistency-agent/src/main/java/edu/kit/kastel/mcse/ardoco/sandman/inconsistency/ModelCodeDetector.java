package edu.kit.kastel.mcse.ardoco.sandman.inconsistency;

import java.nio.file.FileSystems;
import java.nio.file.Path;
import java.nio.file.PathMatcher;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/** Architecture components without linked code and code files without a linked component (T7.3). */
public final class ModelCodeDetector implements InconsistencyDetector {

    static final String MODEL_WITHOUT_CODE = "model-element-without-code";
    static final String CODE_WITHOUT_MODEL = "code-without-model-element";

    static final String GUARD = """
            MATCH (:Project {id: $p})-[:HAS_ARTIFACT]->(:Artifact)-[:CURRENT_VERSION]->(:ArtifactVersion)
                  -[:HAS_ELEMENT]->(c:CodeUnit)<-[:SOURCE|TARGET]-(l:TraceLink {projectId: $p, active: true})
                  -[:SOURCE|TARGET]->(m:ModelElement)<-[:HAS_ELEMENT]-(:ArtifactVersion)<-[:CURRENT_VERSION]-(:Artifact)
            WHERE l.status <> 'rejected' AND ($all OR l.approach IN $approaches)
            RETURN count(l) AS links
            """;

    static final String MODEL_QUERY = """
            MATCH (:Project {id: $p})-[:HAS_ARTIFACT]->(a:Artifact)-[:CURRENT_VERSION]->(:ArtifactVersion)
                  -[:HAS_ELEMENT]->(m:ModelElement)
            WHERE a.kind IN $modelKinds AND m.kind IN $modelElementKinds
            OPTIONAL MATCH (m)<-[:SOURCE|TARGET]-(l:TraceLink {projectId: $p, active: true})-[:SOURCE|TARGET]->(c:CodeUnit)
                  <-[:HAS_ELEMENT]-(:ArtifactVersion)<-[:CURRENT_VERSION]-(:Artifact)
            WHERE l.status <> 'rejected' AND ($all OR l.approach IN $approaches)
            WITH m, count(l) AS links
            WHERE links = 0
            RETURN m.key AS key, m.name AS name, m.inferred AS inferred
            ORDER BY key
            """;

    static final String CODE_QUERY = """
            MATCH (:Project {id: $p})-[:HAS_ARTIFACT]->(a:Artifact)-[:CURRENT_VERSION]->(:ArtifactVersion)
                  -[:HAS_ELEMENT]->(f:CodeUnit)
            WHERE a.kind IN $codeKinds AND f.kind IN $codeElementKinds
            OPTIONAL MATCH (f)-[:CONTAINS*0..]->(:CodeUnit)<-[:SOURCE|TARGET]-(l:TraceLink {projectId: $p, active: true})
                  -[:SOURCE|TARGET]->(m:ModelElement)<-[:HAS_ELEMENT]-(:ArtifactVersion)<-[:CURRENT_VERSION]-(:Artifact)
            WHERE l.status <> 'rejected' AND ($all OR l.approach IN $approaches)
            WITH f, count(l) AS links
            WHERE links = 0
            RETURN f.key AS key
            ORDER BY key
            """;

    @Override
    public String id() {
        return "model-code";
    }

    @Override
    public String version() {
        return "1";
    }

    @Override
    public Set<String> types() {
        return Set.of(MODEL_WITHOUT_CODE, CODE_WITHOUT_MODEL);
    }

    @Override
    public String description() {
        return "Architecture components without linked code and code files without a linked component";
    }

    @Override
    public Set<String> paramKeys() {
        return Set.of(
                "modelKinds",
                "codeKinds",
                "modelElementKinds",
                "codeElementKinds",
                "excludeGlobs",
                "linkApproaches",
                "maxFindingsPerType");
    }

    @Override
    public ScopeMode scopeMode() {
        return ScopeMode.WHOLE_PROJECT;
    }

    @Override
    public List<Finding> detect(DetectionScope scope, DetectorContext ctx) {
        return detectWithWarnings(scope, ctx).findings();
    }

    @Override
    public Detection detectWithWarnings(DetectionScope scope, DetectorContext ctx) {
        Map<String, Object> params = ctx.params();
        Object approachesParam = params.getOrDefault("linkApproaches", "all");
        boolean all = "all".equals(approachesParam);
        List<String> approaches = all ? List.of() : strings(approachesParam, List.of());
        int max = params.get("maxFindingsPerType") instanceof Number n ? n.intValue() : 500;
        Map<String, Object> base = new LinkedHashMap<>();
        base.put("p", scope.projectId());
        base.put("all", all);
        base.put("approaches", approaches);

        long links = ((Number) ctx.kg().read(GUARD, base).getFirst().get("links")).longValue();
        if (links == 0) {
            return new Detection(
                    List.of(), List.of("model-code: no model-code links; enable transarc or declare links (T7.2)"));
        }

        List<String> warnings = new ArrayList<>();
        List<Finding> findings = new ArrayList<>();

        Map<String, Object> modelParams = new LinkedHashMap<>(base);
        modelParams.put("modelKinds", strings(params.get("modelKinds"), List.of("architecture-model")));
        modelParams.put("modelElementKinds", strings(params.get("modelElementKinds"), List.of("component")));
        var modelRows = ctx.kg().read(MODEL_QUERY, modelParams);
        var modelFindings = modelRows.stream()
                .sorted(Comparator.comparing(r -> (String) r.get("key")))
                .map(r -> {
                    boolean inferred = Boolean.TRUE.equals(r.get("inferred"));
                    return new Finding(
                            MODEL_WITHOUT_CODE,
                            inferred ? "info" : "warning",
                            title("Model element without code: ", String.valueOf(r.get("name"))),
                            "The model element has no active, non-rejected link to a code unit.",
                            null,
                            List.of(Finding.Involved.element((String) r.get("key"), "subject")),
                            evidence(inferred, all ? "all" : approaches));
                })
                .toList();
        findings.addAll(cap(modelFindings, MODEL_WITHOUT_CODE, max, warnings));

        Map<String, Object> codeParams = new LinkedHashMap<>(base);
        codeParams.put("codeKinds", strings(params.get("codeKinds"), List.of("code")));
        codeParams.put("codeElementKinds", strings(params.get("codeElementKinds"), List.of("file")));
        List<PathMatcher> excluded =
                strings(params.get("excludeGlobs"), List.of("**/package-info.java", "**/module-info.java")).stream()
                        .map(g -> FileSystems.getDefault().getPathMatcher("glob:" + g))
                        .toList();
        var codeFindings = ctx.kg().read(CODE_QUERY, codeParams).stream()
                .map(r -> (String) r.get("key"))
                .sorted()
                .map(key -> Map.entry(key, key.substring(key.indexOf('#') + 1)))
                .filter(e -> excluded.stream().noneMatch(m -> m.matches(Path.of("/" + e.getValue()))))
                .map(e -> new Finding(
                        CODE_WITHOUT_MODEL,
                        "info",
                        title("Code without model element: ", e.getValue()),
                        "The code file has no active, non-rejected link to a model element.",
                        null,
                        List.of(Finding.Involved.element(e.getKey(), "subject")),
                        Map.of("path", e.getValue())))
                .toList();
        findings.addAll(cap(codeFindings, CODE_WITHOUT_MODEL, max, warnings));
        return new Detection(findings, warnings);
    }

    private static Map<String, Object> evidence(boolean inferred, Object approaches) {
        Map<String, Object> evidence = new LinkedHashMap<>();
        evidence.put("inferredModel", inferred);
        evidence.put("linkApproaches", approaches);
        return evidence;
    }

    private static List<Finding> cap(List<Finding> found, String type, int max, List<String> warnings) {
        if (found.size() <= max) {
            return found;
        }
        warnings.add("model-code: " + type + " capped at " + max + " of " + found.size());
        return found.subList(0, max);
    }

    private static String title(String prefix, String text) {
        String title = prefix + text;
        return title.substring(0, Math.min(200, title.length()));
    }

    private static List<String> strings(Object value, List<String> fallback) {
        if (value == null) {
            return fallback;
        }
        if (value instanceof List<?> list) {
            return list.stream().map(String::valueOf).toList();
        }
        return List.of(String.valueOf(value));
    }
}
