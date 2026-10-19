package edu.kit.kastel.mcse.ardoco.sandman.tlr;

import edu.kit.kastel.mcse.ardoco.sandman.agent.ArtifactStore;
import edu.kit.kastel.mcse.ardoco.sandman.agent.BudgetExceededException;
import edu.kit.kastel.mcse.ardoco.sandman.agent.Ids;
import edu.kit.kastel.mcse.ardoco.sandman.agent.Jcs;
import edu.kit.kastel.mcse.ardoco.sandman.agent.KgClient;
import edu.kit.kastel.mcse.ardoco.sandman.agent.LlmAccess;
import edu.kit.kastel.mcse.ardoco.sandman.agent.LlmClient;
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
import java.util.TreeSet;
import java.util.concurrent.CancellationException;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.locks.ReentrantLock;
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

    /**
     * One lock per (project, approach) for whole-project runs: snapshot, run and finish must not interleave, or a
     * stale snapshot retires the links of a newer run. ponytail: locks are never evicted (bounded by projects x
     * approaches); lock is per JVM, so more than one tlr-agent replica needs a KG-side lease.
     */
    private final Map<String, ReentrantLock> wholeProjectLocks = new ConcurrentHashMap<>();

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
        for (Approaches.Entry entry : selected) {
            Set<String> allowed = entry.approach().paramKeys();
            ObjectNode effective = Approaches.effective(
                    entry.params(), input.path("params").path(entry.approach().id()));
            if (allowed != null && !allowed.containsAll(effective.propertyNames())) {
                throw new SkillException(
                        "INVALID_INPUT",
                        "approach " + entry.approach().id() + " accepts parameters " + new TreeSet<>(allowed) + ", got "
                                + new TreeSet<>(effective.propertyNames()),
                        false);
            }
        }

        LinkStore store = new LinkStore(kg);
        TlrInput pairwise = null;
        RunRecorder recorder =
                new RunRecorder(kg, settings.serviceName(), System.getenv().getOrDefault("SANDMAN_VERSION", "dev"));
        String llmPolicy = selected.stream().anyMatch(e -> e.approach().usesLlm()) ? llmPolicy(projectId) : null;
        ArrayNode runs = JsonMapper.shared().createArrayNode();
        Set<String> changedSources = new TreeSet<>();
        Set<String> changedTargets = new TreeSet<>();
        List<String> unavailable = new ArrayList<>();
        List<String> budget = new ArrayList<>();
        List<String> invalid = new ArrayList<>();
        try {
            for (Approaches.Entry entry : selected) {
                if (ctx.cancelled()) {
                    break;
                }
                if (entry.approach().scopeMode() == ScopeMode.WHOLE_PROJECT) {
                    ReentrantLock lock = wholeProjectLocks.computeIfAbsent(
                            projectId + '\0' + entry.approach().id(), k -> new ReentrantLock());
                    if (!lockUnlessCancelled(lock, ctx)) {
                        break;
                    }
                    try {
                        TlrInput in = wholeProject(projectId, entry.approach(), store);
                        ObjectNode run = runApproach(
                                ctx,
                                input,
                                entry,
                                in,
                                sourceIds,
                                targetIds,
                                store,
                                recorder,
                                llmPolicy,
                                changedSources,
                                changedTargets,
                                unavailable,
                                budget,
                                invalid);
                        if (run != null) {
                            runs.add(run);
                        }
                    } finally {
                        lock.unlock();
                    }
                    continue;
                }
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
                ObjectNode run = runApproach(
                        ctx,
                        input,
                        entry,
                        pairwise,
                        sourceIds,
                        targetIds,
                        store,
                        recorder,
                        llmPolicy,
                        changedSources,
                        changedTargets,
                        unavailable,
                        budget,
                        invalid);
                if (run != null) {
                    runs.add(run);
                }
            }
        } finally {
            selected.forEach(e -> e.approach().callFinished());
        }
        if (!budget.isEmpty()) { // the orchestrator blocks the workflow; the other approaches are finished
            throw new SkillException("BUDGET_EXCEEDED", String.join("; ", budget), false);
        }
        if (!invalid.isEmpty()) {
            throw new SkillException("INVALID_INPUT", String.join("; ", invalid), false);
        }
        if (!unavailable.isEmpty()) { // the other approaches are finished and their links written
            throw new SkillException("DEPENDENCY_UNAVAILABLE", String.join("; ", unavailable), true);
        }
        ObjectNode output = JsonMapper.shared().createObjectNode();
        output.set("runs", runs);
        ObjectNode event = JsonMapper.shared().createObjectNode();
        event.put("projectId", projectId);
        event.set("sourceVersionIds", withChanged(sourceIds, changedSources));
        event.set("targetVersionIds", withChanged(targetIds, changedTargets));
        event.set("runs", runs);
        return new SkillResult(output, "sandman.tracelinks.updated", event);
    }

    private static boolean lockUnlessCancelled(ReentrantLock lock, SkillContext ctx) {
        try {
            while (!lock.tryLock(100, TimeUnit.MILLISECONDS)) {
                if (ctx.cancelled()) {
                    return false;
                }
            }
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            return false;
        }
        return true;
    }

    /** The input ids, then the changed ids that are not among them. */
    private static ArrayNode withChanged(List<String> input, Set<String> changed) {
        ArrayNode out = JsonMapper.shared().createArrayNode();
        new LinkedHashSet<>(input).forEach(out::add);
        changed.stream().filter(c -> !input.contains(c)).forEach(out::add);
        return out;
    }

    /** The Run summary; null if the approach's environment is unavailable or the task was cancelled before the Run started. */
    private ObjectNode runApproach(
            SkillContext ctx,
            JsonNode input,
            Approaches.Entry entry,
            TlrInput in,
            List<String> sourceIds,
            List<String> targetIds,
            LinkStore store,
            RunRecorder recorder,
            String llmPolicy,
            Set<String> changedSources,
            Set<String> changedTargets,
            List<String> unavailable,
            List<String> budget,
            List<String> invalid) {
        TraceLinkApproach approach = entry.approach();
        ObjectNode params =
                Approaches.effective(entry.params(), input.path("params").path(approach.id()));
        ObjectNode hashed = params.deepCopy();
        String environment;
        LlmAccess llm = null;
        List<String> promptIds;
        try {
            llm = approach.usesLlm() ? LlmAccess.forPolicy(settings, llmPolicy) : null;
            var envCtx =
                    new TlrContext(llm, null, ctx::cancelled, LoggerFactory.getLogger(approach.getClass()), params);
            environment = approach.environment(envCtx);
            promptIds = approach.promptIds(envCtx);
        } catch (LlmAccess.PolicyException
                | Settings.ConfigException e) { // no configured model, so no configHash and no Run
            LOG.error("approach {} violates the LLM policy", approach.id(), e);
            invalid.add(approach.id() + ": " + e.getMessage());
            return null;
        } catch (CancellationException e) {
            LOG.info("approach {} cancelled", approach.id());
            return null;
        } catch (ApproachUnavailableException e) { // no environment, no configHash, so no Run
            LOG.error("approach {} unavailable", approach.id(), e);
            unavailable.add(approach.id() + ": " + e.getMessage());
            return null;
        }
        if (environment != null) {
            hashed.put("environment", environment);
        }
        String configHash = Ids.sha256Hex(Jcs.canonicalize(hashed));
        ObjectNode keyed = JsonMapper.shared().createObjectNode();
        keyed.set("input", input);
        keyed.put("tool", approach.id());
        keyed.put("configHash", configHash);
        if (!promptIds.isEmpty()) {
            ArrayNode ids = keyed.putArray("promptIds");
            promptIds.forEach(ids::add);
        }
        String inputKey = Ids.sha256Hex(Jcs.canonicalize(keyed));
        recorder.cleanupStale(inputKey);
        String runId = recorder.start(new RunInfo(
                in.projectId(),
                SKILL,
                approach.id(),
                approach.version(),
                inputKey,
                llm != null ? llm.chatRoute() : null,
                params.toString(),
                configHash,
                in.versions().stream().map(TlrInput.Version::id).toList(),
                ctx.workflowId(),
                ctx.taskId(),
                ctx.actor() != null ? ctx.actor() : "service:" + ctx.caller(),
                null,
                promptIds));
        ObjectNode run = JsonMapper.shared().createObjectNode();
        run.put("approach", approach.id());
        run.put("approachVersion", approach.version());
        run.put("runId", runId);
        LlmClient client = llm != null ? new LlmClient(llm, runId) : null;
        try {
            ArtifactStore artifacts =
                    settings.artifactStore() == null ? null : new ArtifactStore(settings.artifactStore());
            var tlrCtx = new TlrContext(
                    llm,
                    artifacts,
                    ctx::cancelled,
                    LoggerFactory.getLogger(approach.getClass()),
                    params,
                    client,
                    ctx::progress);
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
                LinkStore.Changed changed =
                        store.finishWholeProject(runId, in.projectId(), approach.id(), approach.supports());
                changedSources.addAll(changed.sources());
                changedTargets.addAll(changed.targets());
            } else {
                store.finish(runId, in.projectId(), approach.id(), sourceIds, targetIds);
            }
            if (client != null) {
                recorder.complete(
                        runId,
                        "completed",
                        client.inputTokens(),
                        client.outputTokens(),
                        client.resolvedModel(),
                        client.mixedModels());
            }
            run.put("status", "completed");
            run.put("linkCount", links.size());
        } catch (CancellationException e) { // the A2A layer reports the task as canceled and drops this summary
            LOG.info("approach {} cancelled", approach.id());
            fail(recorder, runId, "cancelled", client); // the KG Run status has no cancelled value
            run.put("status", "cancelled");
            run.put("linkCount", 0);
        } catch (ApproachUnavailableException e) { // the skill fails retryable after the other approaches
            LOG.error("approach {} unavailable", approach.id(), e);
            String error = e.getClass().getSimpleName() + ": " + e.getMessage();
            fail(recorder, runId, error, client);
            unavailable.add(approach.id() + ": " + e.getMessage());
            run.put("status", "failed");
            run.put("linkCount", 0);
            run.put("error", error);
        } catch (BudgetExceededException e) { // the skill fails non-retryable after the other approaches
            LOG.error("approach {} exceeded the LLM budget", approach.id(), e);
            String error = "BUDGET_EXCEEDED: " + e.getMessage();
            fail(recorder, runId, error, client);
            budget.add(approach.id() + ": " + e.getMessage());
            run.put("status", "failed");
            run.put("linkCount", 0);
            run.put("error", error);
        } catch (RuntimeException e) { // isolates this approach; its links stay inactive
            LOG.error("approach {} failed", approach.id(), e);
            String error = e.getClass().getSimpleName() + ": " + e.getMessage();
            fail(recorder, runId, error, client);
            run.put("status", "failed");
            run.put("linkCount", 0);
            run.put("error", error);
        }
        return run;
    }

    private static void fail(RunRecorder recorder, String runId, String error, LlmClient client) {
        if (client == null) {
            recorder.fail(runId, error);
        } else {
            recorder.fail(
                    runId,
                    error,
                    client.inputTokens(),
                    client.outputTokens(),
                    client.resolvedModel(),
                    client.mixedModels());
        }
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
