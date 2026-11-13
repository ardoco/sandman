package edu.kit.kastel.mcse.ardoco.sandman.code;

import edu.kit.kastel.mcse.ardoco.core.api.models.CodeModel;
import edu.kit.kastel.mcse.ardoco.core.api.models.Metamodel;
import edu.kit.kastel.mcse.ardoco.core.api.models.code.CodeItemRepository;
import edu.kit.kastel.mcse.ardoco.core.common.IdentifierProvider;
import edu.kit.kastel.mcse.ardoco.sandman.agent.Settings;
import edu.kit.kastel.mcse.ardoco.sandman.agent.a2a.SkillContext;
import edu.kit.kastel.mcse.ardoco.sandman.agent.a2a.SkillException;
import edu.kit.kastel.mcse.ardoco.tlr.models.connectors.generators.code.AllLanguagesExtractor;
import io.quarkus.runtime.StartupEvent;
import jakarta.enterprise.context.ApplicationScoped;
import jakarta.enterprise.event.Observes;
import jakarta.inject.Inject;
import java.io.IOException;
import java.io.UncheckedIOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashSet;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.locks.ReentrantLock;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ArrayNode;
import tools.jackson.databind.node.ObjectNode;

/**
 * Stages the files of a call, runs ArDoCo's {@code AllLanguagesExtractor} and maps or writes the result. ArDoCo's
 * ids come from one JVM-global counter, so extractions run one at a time and reset it first: the same input then
 * gives the same ACM.
 */
@ApplicationScoped
public class CodeService {

    private static final Logger LOG = LoggerFactory.getLogger(CodeService.class);
    static final ReentrantLock LOCK = new ReentrantLock();

    @Inject
    Settings settings;

    private Limits limits = Limits.fromEnv(System.getenv());
    private long lockTimeoutMillis =
            Limits.number(System.getenv(), "SANDMAN_CODE_LOCK_TIMEOUT_SECONDS", 300, 86_400) * 1000;

    void onStart(@Observes StartupEvent event) {
        settings.require("pluginInboundTokens", "artifactStore");
        LOG.info("code-extractor-ardoco ready, limits {}", limits);
    }

    /** Each skill has a fixed set of callers; the token already identified it (one inbound token per caller). */
    static void requireCaller(SkillContext ctx, String... expected) throws SkillException {
        if (!java.util.List.of(expected).contains(ctx.caller())) {
            throw new SkillException("FORBIDDEN", "this skill is only for " + String.join(", ", expected), false);
        }
    }

    /** For tests. */
    void limits(Limits limits) {
        this.limits = limits;
    }

    /** For tests. */
    void lockTimeoutMillis(long millis) {
        this.lockTimeoutMillis = millis;
    }

    /** {@code PluginExtractOutput} of a {@code PluginExtractInput}. */
    JsonNode extract(JsonNode input) throws SkillException {
        List<Staging.Item> items = new ArrayList<>();
        input.path("context").path("currentArtifacts").forEach(a -> items.add(item(a)));
        List<ElementMapping.Artifact> artifacts = new ArrayList<>();
        for (JsonNode a : input.path("artifacts")) {
            items.add(item(a));
            artifacts.add(new ElementMapping.Artifact(
                    a.get("path").asString(), a.get("kind").asString()));
        }
        Set<String> current = new LinkedHashSet<>();
        items.forEach(i -> current.add(i.path()));
        return withModel(
                items,
                (model, extractor) ->
                        ElementMapping.map(model, artifacts, current).toJson());
    }

    /** {@code PluginCodeModelOutput} of a {@code PluginCodeModelInput}. */
    JsonNode codeModel(JsonNode input) throws SkillException {
        List<Staging.Item> items = new ArrayList<>();
        input.path("currentArtifacts").forEach(a -> items.add(item(a)));
        return withModel(items, (model, extractor) -> {
            try {
                Path file = Files.createTempFile("acm-", ".json");
                try {
                    extractor.writeOutCodeModel(model, file.toFile());
                    ObjectNode out = JsonMapper.shared().createObjectNode();
                    JsonNode acm = JsonMapper.shared().readTree(Files.readAllBytes(file));
                    canonicalizeCodeFiles(acm.path("codeFiles"));
                    out.set("acm", acm);
                    return out;
                } finally {
                    Files.deleteIfExists(file);
                }
            } catch (IOException e) {
                throw new UncheckedIOException(e);
            }
        });
    }

    /**
     * Magika returns its files in an order that depends on the random staging directory, and ArDoCo numbers
     * {@code codeFiles} in that order. Sort them by path and reuse the same ids in that order, so equal input gives an
     * equal ACM. Upstream also lists a unit once for each package that contains it (nested packages are endpoints too),
     * so drop repeated entries of the same path and unit.
     */
    static void canonicalizeCodeFiles(JsonNode codeFiles) {
        if (!(codeFiles instanceof ArrayNode array)) {
            return;
        }
        List<ObjectNode> files = new ArrayList<>();
        Set<String> seen = new HashSet<>();
        array.forEach(f -> {
            if (seen.add(codeFilePath((ObjectNode) f) + "\0"
                    + f.path("compilationUnitId").asString(""))) {
                files.add((ObjectNode) f);
            }
        });
        List<String> ids =
                files.stream().map(f -> f.path("id").asString()).sorted().toList();
        files.sort(Comparator.comparing(CodeService::codeFilePath)
                .thenComparing(f -> f.path("compilationUnitId").asString("")));
        array.removeAll();
        for (int i = 0; i < files.size(); i++) {
            files.get(i).put("id", ids.get(i));
            array.add(files.get(i));
        }
    }

    private static String codeFilePath(ObjectNode f) {
        List<String> parts = new ArrayList<>();
        f.path("pathElements").forEach(e -> parts.add(e.asString()));
        parts.add(f.path("name").asString()
                + (f.path("extension").asString("").isEmpty()
                        ? ""
                        : "." + f.path("extension").asString()));
        return String.join("/", parts);
    }

    private static Staging.Item item(JsonNode a) {
        return new Staging.Item(a.get("path").asString(), a.get("storagePath").asString());
    }

    private interface Body {
        JsonNode apply(CodeModel model, AllLanguagesExtractor extractor);
    }

    private JsonNode withModel(List<Staging.Item> items, Body body) throws SkillException {
        Path store = settings.artifactStore();
        try {
            if (!LOCK.tryLock(lockTimeoutMillis, TimeUnit.MILLISECONDS)) {
                throw new SkillException(
                        "DEPENDENCY_UNAVAILABLE", "another code extraction is running, retry later", true);
            }
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new SkillException("INTERNAL", "interrupted while waiting for another code extraction", true);
        }
        try {
            purgeMagikaTemporaryFiles();
            try (Staging staging = Staging.stage(store, items, limits)) {
                IdentifierProvider.reset();
                var extractor = new AllLanguagesExtractor(
                        new CodeItemRepository(),
                        staging.root().toString(),
                        Metamodel.CODE_WITH_COMPILATION_UNITS_AND_PACKAGES);
                CodeModel model = extractor.extractModel();
                return body.apply(model, extractor);
            }
        } catch (RuntimeException e) {
            LOG.error("ArDoCo code extraction failed", e);
            throw new SkillException(
                    "INTERNAL",
                    "code extraction failed: " + e.getClass().getSimpleName() + ": " + e.getMessage(),
                    false);
        } finally {
            purgeMagikaTemporaryFiles();
            LOCK.unlock();
        }
    }

    /**
     * Magika writes one {@code tmp_magika*.tmp} file per classified file into java.io.tmpdir and never deletes it. They
     * hold customer code and fill the temp filesystem, so every call removes them (calls run one at a time).
     */
    static void purgeMagikaTemporaryFiles() {
        try (var files = Files.newDirectoryStream(Path.of(System.getProperty("java.io.tmpdir")), "tmp_magika*.tmp")) {
            for (Path file : files) {
                Files.deleteIfExists(file);
            }
        } catch (IOException e) {
            LOG.warn("cannot remove Magika temporary files: {}", e.toString());
        }
    }
}
