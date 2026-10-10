package edu.kit.kastel.mcse.ardoco.sandman.tlr;

import edu.kit.kastel.mcse.ardoco.sandman.agent.ArtifactStore;
import edu.kit.kastel.mcse.ardoco.sandman.agent.Ids;
import edu.kit.kastel.mcse.ardoco.sandman.agent.Jcs;
import edu.kit.kastel.mcse.ardoco.sandman.agent.KgClient;
import edu.kit.kastel.mcse.ardoco.sandman.agent.LlmAccess;
import edu.kit.kastel.mcse.ardoco.sandman.agent.RunRecorder;
import edu.kit.kastel.mcse.ardoco.sandman.agent.RunRecorder.RunInfo;
import edu.kit.kastel.mcse.ardoco.sandman.agent.Settings;
import edu.kit.kastel.mcse.ardoco.sandman.agent.a2a.Skill;
import edu.kit.kastel.mcse.ardoco.sandman.agent.a2a.SkillContext;
import edu.kit.kastel.mcse.ardoco.sandman.agent.a2a.SkillException;
import edu.kit.kastel.mcse.ardoco.sandman.agent.a2a.SkillResult;
import jakarta.inject.Inject;
import jakarta.inject.Singleton;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.stream.Stream;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ArrayNode;
import tools.jackson.databind.node.ObjectNode;

/** Skill recover-trace-links: one Run per approach, one completion event for all runs. */
@Singleton
public class RecoverTraceLinks implements Skill {

    static final String SKILL = "recover-trace-links";
    private static final Logger LOG = LoggerFactory.getLogger(RecoverTraceLinks.class);

    @Inject
    KgClient kg;

    @Inject
    Settings settings;

    @Inject
    Approaches approaches;

    @Override
    public String id() {
        return SKILL;
    }

    @Override
    public SkillResult run(SkillContext ctx, JsonNode input) throws SkillException {
        String projectId = input.get("projectId").asString();
        List<String> sourceIds = strings(input.get("sourceVersionIds"));
        List<String> targetIds = strings(input.get("targetVersionIds"));
        Map<String, String> kinds = versionKinds(
                projectId,
                Stream.concat(sourceIds.stream(), targetIds.stream()).distinct().toList());
        Set<KindPair> pairs = new LinkedHashSet<>();
        sourceIds.forEach(s -> targetIds.forEach(t -> pairs.add(new KindPair(kinds.get(s), kinds.get(t)))));
        List<Approaches.Entry> selected = select(input, pairs);

        LinkStore store = new LinkStore(kg);
        TlrInput pairwise = null;
        RunRecorder recorder =
                new RunRecorder(kg, settings.serviceName(), System.getenv().getOrDefault("SANDMAN_VERSION", "dev"));
        String llmPolicy = selected.stream().anyMatch(e -> e.approach().usesLlm()) ? llmPolicy(projectId) : null;
        ArrayNode runs = JsonMapper.shared().createArrayNode();
        for (Approaches.Entry entry : selected) {
            if (ctx.cancelled()) {
                break;
            }
            TlrInput in;
            if (entry.approach().scopeMode() == ScopeMode.WHOLE_PROJECT) {
                in = wholeProject(projectId, entry.approach(), store);
            } else {
                if (pairwise == null) {
                    pairwise = new TlrInput(
                            projectId,
                            store.versions(
                                    projectId,
                                    Stream.concat(sourceIds.stream(), targetIds.stream())
                                            .distinct()
                                            .toList()),
                            store.elements(projectId, sourceIds),
                            store.elements(projectId, targetIds));
                }
                in = pairwise;
            }
            runs.add(runApproach(ctx, input, entry, in, sourceIds, targetIds, store, recorder, llmPolicy));
        }
        ObjectNode output = JsonMapper.shared().createObjectNode();
        output.set("runs", runs);
        ObjectNode event = JsonMapper.shared().createObjectNode();
        event.put("projectId", projectId);
        event.set("sourceVersionIds", input.get("sourceVersionIds"));
        event.set("targetVersionIds", input.get("targetVersionIds"));
        event.set("runs", runs);
        return new SkillResult(output, "sandman.tracelinks.updated", event);
    }

    private ObjectNode runApproach(
            SkillContext ctx,
            JsonNode input,
            Approaches.Entry entry,
            TlrInput in,
            List<String> sourceIds,
            List<String> targetIds,
            LinkStore store,
            RunRecorder recorder,
            String llmPolicy) {
        TraceLinkApproach approach = entry.approach();
        ObjectNode params =
                Approaches.effective(entry.params(), input.path("params").path(approach.id()));
        String configHash = Ids.sha256Hex(Jcs.canonicalize(params));
        ObjectNode keyed = JsonMapper.shared().createObjectNode();
        keyed.set("input", input);
        keyed.put("tool", approach.id());
        keyed.put("configHash", configHash);
        String inputKey = Ids.sha256Hex(Jcs.canonicalize(keyed));
        recorder.cleanupStale(inputKey);
        String runId = recorder.start(new RunInfo(
                in.projectId(),
                SKILL,
                approach.id(),
                approach.version(),
                inputKey,
                null,
                params.toString(),
                configHash,
                in.versions().stream().map(TlrInput.Version::id).toList(),
                ctx.workflowId(),
                ctx.taskId(),
                ctx.actor() != null ? ctx.actor() : "service:" + ctx.caller(),
                null));
        ObjectNode run = JsonMapper.shared().createObjectNode();
        run.put("approach", approach.id());
        run.put("approachVersion", approach.version());
        run.put("runId", runId);
        try {
            LlmAccess llm = approach.usesLlm() ? LlmAccess.forPolicy(settings, llmPolicy) : null;
            ArtifactStore artifacts =
                    settings.artifactStore() == null ? null : new ArtifactStore(settings.artifactStore());
            var tlrCtx = new TlrContext(
                    llm, artifacts, ctx::cancelled, LoggerFactory.getLogger(approach.getClass()), params);
            Map<String, Map<String, Object>> links = new LinkedHashMap<>();
            for (CandidateLink c : approach.recover(in, tlrCtx)) {
                String id =
                        Ids.stableId("tl", approach.id(), approach.version(), configHash, c.sourceId(), c.targetId());
                Map<String, Object> l = new HashMap<>();
                l.put("id", id);
                l.put("sourceId", c.sourceId());
                l.put("targetId", c.targetId());
                l.put("linkType", c.linkType());
                l.put("confidence", c.confidence());
                l.put("rationale", c.rationale());
                links.put(id, l);
            }
            store.writeInactive(runId, in.projectId(), approach, configHash, new ArrayList<>(links.values()));
            if (approach.scopeMode() == ScopeMode.WHOLE_PROJECT) {
                store.finishWholeProject(runId, in.projectId(), approach.id(), approach.supports());
            } else {
                store.finish(runId, in.projectId(), approach.id(), sourceIds, targetIds);
            }
            run.put("status", "completed");
            run.put("linkCount", links.size());
        } catch (RuntimeException e) { // isolates this approach; its links stay inactive
            LOG.error("approach {} failed", approach.id(), e);
            String error = e.getClass().getSimpleName() + ": " + e.getMessage();
            recorder.fail(runId, error);
            run.put("status", "failed");
            run.put("linkCount", 0);
            run.put("error", error);
        }
        return run;
    }

    /** All current versions of the kinds the approach supports; the requested version subset is ignored (R-022). */
    private static TlrInput wholeProject(String projectId, TraceLinkApproach approach, LinkStore store) {
        Set<String> sourceKinds = new LinkedHashSet<>();
        Set<String> targetKinds = new LinkedHashSet<>();
        approach.supports().forEach(p -> {
            sourceKinds.add(p.source());
            targetKinds.add(p.target());
        });
        List<TlrInput.Version> sources = store.currentVersions(projectId, sourceKinds);
        List<TlrInput.Version> targets = store.currentVersions(projectId, targetKinds);
        List<TlrInput.Version> versions =
                Stream.concat(sources.stream(), targets.stream()).distinct().toList();
        return new TlrInput(
                projectId,
                versions,
                store.elements(
                        projectId, sources.stream().map(TlrInput.Version::id).toList()),
                store.elements(
                        projectId, targets.stream().map(TlrInput.Version::id).toList()));
    }

    private List<Approaches.Entry> select(JsonNode input, Set<KindPair> pairs) throws SkillException {
        if (!input.has("approaches")) {
            return approaches.enabled().stream()
                    .filter(e -> e.approach().supports().stream().anyMatch(pairs::contains))
                    .toList();
        }
        List<Approaches.Entry> selected = new ArrayList<>();
        for (String id : new LinkedHashSet<>(strings(input.get("approaches")))) {
            Approaches.Entry entry = approaches.get(id);
            if (entry == null) {
                throw new SkillException("INVALID_INPUT", "unknown or disabled approach " + id, false);
            }
            selected.add(entry);
        }
        return selected;
    }

    /** Artifact kind per version id; fails if a version is unknown or belongs to another project. */
    private Map<String, String> versionKinds(String projectId, List<String> ids) throws SkillException {
        Map<String, String> kinds = new HashMap<>();
        kg.read(
                        "MATCH (a:Artifact)-[:HAS_VERSION]->(v:ArtifactVersion) WHERE v.projectId = $p AND v.id IN $ids "
                                + "RETURN v.id AS id, a.kind AS kind",
                        Map.of("p", projectId, "ids", ids))
                .forEach(r -> kinds.put((String) r.get("id"), (String) r.get("kind")));
        if (kinds.size() != ids.size()) {
            throw new SkillException("INVALID_INPUT", "unknown artifact versions in project " + projectId, false);
        }
        return kinds;
    }

    private String llmPolicy(String projectId) {
        var rows = kg.read("MATCH (p:Project {id: $p}) RETURN p.llmPolicy AS policy", Map.of("p", projectId));
        return rows.isEmpty() ? null : (String) rows.get(0).get("policy");
    }

    private static List<String> strings(JsonNode array) {
        return array.valueStream().map(JsonNode::asString).toList();
    }
}
