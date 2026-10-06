import java.io.File;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.security.MessageDigest;
import java.text.Normalizer;
import java.util.ArrayList;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;
import java.util.TreeSet;
import java.util.stream.Collectors;

import org.eclipse.collections.api.factory.SortedMaps;
import org.eclipse.collections.api.map.sorted.ImmutableSortedMap;

import edu.kit.kastel.mcse.ardoco.core.api.models.Metamodel;
import edu.kit.kastel.mcse.ardoco.core.api.models.ModelFormat;
import edu.kit.kastel.mcse.ardoco.core.api.output.ArdocoResult;
import edu.kit.kastel.mcse.ardoco.core.api.stage.connectiongenerator.ConnectionStates;
import edu.kit.kastel.mcse.ardoco.core.api.stage.inconsistency.Inconsistency;
import edu.kit.kastel.mcse.ardoco.core.api.stage.inconsistency.ModelInconsistency;
import edu.kit.kastel.mcse.ardoco.core.api.stage.inconsistency.TextInconsistency;
import edu.kit.kastel.mcse.ardoco.core.api.text.Sentence;
import edu.kit.kastel.mcse.ardoco.core.common.similarity.SimilarityUtils;
import edu.kit.kastel.mcse.ardoco.core.common.util.DataRepositoryHelper;
import edu.kit.kastel.mcse.ardoco.core.data.DataRepository;
import edu.kit.kastel.mcse.ardoco.core.execution.Ardoco;
import edu.kit.kastel.mcse.ardoco.core.pipeline.AbstractPipelineStep;
import edu.kit.kastel.mcse.ardoco.core.pipeline.agent.Claimant;
import edu.kit.kastel.mcse.ardoco.id.InconsistencyChecker;
import edu.kit.kastel.mcse.ardoco.id.execution.runner.InconsistencyDetection;
import edu.kit.kastel.mcse.ardoco.tlr.connectiongenerator.ConnectionGenerator;
import edu.kit.kastel.mcse.ardoco.tlr.connectiongenerator.ConnectionStatesImpl;
import edu.kit.kastel.mcse.ardoco.tlr.models.agents.ArchitectureConfiguration;
import edu.kit.kastel.mcse.ardoco.tlr.models.agents.ModelProviderAgent;
import edu.kit.kastel.mcse.ardoco.tlr.recommendationgenerator.RecommendationGenerator;
import edu.kit.kastel.mcse.ardoco.tlr.text.providers.TextPreprocessingAgent;
import edu.kit.kastel.mcse.ardoco.tlr.textextraction.TextExtraction;

/**
 * Spike T0.12. Modes:
 * <pre>
 *   detect    TEXT PCM_REPOSITORY OUT_DIR [GOLD_SAD_SAM_CSV]   run InconsistencyDetection (+ external-link variants)
 *   presplit  TEXT_ONE_SENTENCE_PER_LINE PCM_REPOSITORY OUT_DIR   InconsistencyDetection on a text pre-annotated with ssplit.eolonly
 *   sentences TEXT...                                           CoreNLP sentence split + sentence-key hash analysis
 * </pre>
 */
public class Spike {
    private static final ImmutableSortedMap<String, String> NO_CONFIG = SortedMaps.immutable.empty();

    public static void main(String[] args) throws Exception {
        selfCheck();
        switch (args[0]) {
        case "detect" -> detect(new File(args[1]), new File(args[2]), new File(args[3]), args.length > 4 ? new File(args[4]) : null);
        case "presplit" -> presplit(new File(args[1]), new File(args[2]), new File(args[3]));
        case "sentences" -> {
            for (int i = 1; i < args.length; i++) {
                sentences(new File(args[i]));
            }
        }
        default -> throw new IllegalArgumentException(args[0]);
        }
    }

    // ---------------------------------------------------------------- sentence keys (contracts/conformance/vectors.json)

    static String normalize(String text) {
        return Normalizer.normalize(text, Normalizer.Form.NFC).replaceAll("[ \\t\\n\\r\\f\\u000B]+", " ").replaceAll("^ | $", "");
    }

    static String hash12(String text) throws Exception {
        var digest = MessageDigest.getInstance("SHA-256").digest(normalize(text).getBytes(StandardCharsets.UTF_8));
        return HexFormat.of().formatHex(digest).substring(0, 12);
    }

    /** Vectors sentenceKey cases 1 and 3 (hash part only). */
    static void selfCheck() throws Exception {
        check(hash12("The  MediaCache stores\taudio files. ").equals("703c4e4a5dd6"), "vector 1");
        check(hash12("The MediaCache stores audio files.").equals("703c4e4a5dd6"), "vector 2");
        check(hash12("Größe und Café.").equals("54b850c03299"), "vector 3");
    }

    static void check(boolean ok, String what) {
        if (!ok) {
            throw new AssertionError(what);
        }
    }

    // ---------------------------------------------------------------- V-16 sentences

    static List<Sentence> splitSentences(File text) {
        var ardoco = new Ardoco("sentences");
        var repo = ardoco.getDataRepository();
        DataRepositoryHelper.putInputText(repo, readText(text));
        ardoco.addPipelineStep(TextPreprocessingAgent.get(NO_CONFIG, repo));
        ardoco.run();
        return DataRepositoryHelper.getAnnotatedText(repo).getSentences().castToList();
    }

    static void sentences(File textFile) throws Exception {
        var raw = readText(textFile);
        var sentences = splitSentences(textFile);
        reportSentences(textFile.getName(), raw, sentences, true);
    }

    static void reportSentences(String name, String raw, List<Sentence> sentences, boolean listAll) throws Exception {
        Map<String, List<Integer>> byHash = new LinkedHashMap<>();
        int verbatim = 0, multiLine = 0, numberingOk = 0;
        List<String> notVerbatim = new ArrayList<>();
        for (int i = 0; i < sentences.size(); i++) {
            var s = sentences.get(i);
            if (s.getSentenceNumber() == i) {
                numberingOk++;
            }
            var h = hash12(s.getText());
            byHash.computeIfAbsent(h, k -> new ArrayList<>()).add(s.getSentenceNumber());
            if (raw.contains(s.getText())) {
                verbatim++;
            } else {
                notVerbatim.add(s.getSentenceNumber() + ": " + s.getText());
            }
            if (s.getText().contains("\n")) {
                multiLine++;
            }
            if (listAll) {
                System.out.printf("  S%03d(0-based) %s  %s%n", s.getSentenceNumber(), h, s.getText().replace("\n", "\\n"));
            }
        }
        var collisions = byHash.entrySet().stream().filter(e -> e.getValue().size() > 1).toList();
        System.out.printf("SENTENCES %s: count=%d, rawLines=%d, numbering 0-based contiguous=%s, verbatim substring of raw=%d, containing newline=%d, distinct hashes=%d, collisions=%s%n",
                name, sentences.size(), raw.split("\n", -1).length, numberingOk == sentences.size(), verbatim, multiLine, byHash.size(), collisions);
        notVerbatim.forEach(s -> System.out.println("  NOT VERBATIM " + s));
    }

    // ---------------------------------------------------------------- V-16 option: ArDoCo uses our sentence boundaries

    /** Same annotators/properties as ArDoCo's TextProcessorLocal, plus ssplit.eolonly (one sentence per line). */
    static void presplit(File text, File pcm, File out) throws Exception {
        var raw = readText(text);
        var props = new java.util.Properties();
        props.setProperty("annotators", "tokenize,ssplit,pos,parse,depparse,lemma");
        props.put("parse", "EnhancedPlusPlusDependenciesAnnotation");
        props.put("depparse", "EnhancedPlusPlusDependenciesAnnotation");
        props.put("coref.algorithm", "fastneural");
        props.setProperty("ssplit.eolonly", "true");
        var doc = new edu.stanford.nlp.pipeline.CoreDocument(raw);
        new edu.stanford.nlp.pipeline.StanfordCoreNLP(props).annotate(doc);

        out.mkdirs();
        var runner = new InconsistencyDetection("presplit");
        runner.setUp(text, pcm, format(pcm), NO_CONFIG, out);
        // CoreNLPProvider skips its own processing when PreprocessingData is already present
        DataRepositoryHelper.putPreprocessingData(runner.getArdoco().getDataRepository(),
                new edu.kit.kastel.mcse.ardoco.core.api.PreprocessingData(new edu.kit.kastel.mcse.ardoco.tlr.text.providers.informants.corenlp.TextImpl(doc)));
        var result = runner.run();
        System.out.println("RUN presplit (ssplit.eolonly, injected PreprocessingData)");
        report(result, raw, Files.readString(pcm.toPath()));
        reportSentences(text.getName(), raw, result.getText().getSentences().castToList(), true);
    }

    // ---------------------------------------------------------------- run detection

    static void detect(File text, File pcm, File out, File gold) throws Exception {
        out.mkdirs();
        var raw = readText(text);
        var modelXml = Files.readString(pcm.toPath());

        long t0 = System.nanoTime();
        var runner = new InconsistencyDetection("internal");
        runner.setUp(text, pcm, format(pcm), NO_CONFIG, new File(out, "internal"));
        new File(out, "internal").mkdirs();
        var internal = runner.run();
        System.out.printf("RUN internal (standard InconsistencyDetection runner): %.1f s%n", (System.nanoTime() - t0) / 1e9);
        var internalFindings = report(internal, raw, modelXml);
        reportSentences(text.getName(), raw, internal.getText().getSentences().castToList(), false);
        for (var metamodel : internal.getMetamodels()) {
            var endpoints = internal.getModelState(metamodel).getEndpoints();
            var missing = endpoints.stream().filter(e -> !modelXml.contains("id=\"" + e.getId() + "\"")).map(e -> e.getName() + "=" + e.getId()).toList();
            System.out.printf("MODEL %s: endpoints=%d, ids not found as id=\"...\" in model file: %s, types=%s%n", metamodel, endpoints.size(), missing,
                    endpoints.stream().map(e -> e.getType().orElse("?")).collect(Collectors.toCollection(TreeSet::new)));
        }

        // ArDoCo's own SAD-SAM links reduced to (1-based sentence, model id), i.e. what the KG would store
        Map<Integer, Set<String>> selfLinks = new TreeMap<>();
        internal.getArchitectureTraceLinks()
                .forEach(tl -> selfLinks.computeIfAbsent(tl.getFirstEndpoint().getSentence().getSentenceNumber() + 1, k -> new TreeSet<>())
                        .add(tl.getSecondEndpoint().getId()));
        System.out.println("SAD-SAM links (sentence-level) produced internally: " + selfLinks.values().stream().mapToInt(Set::size).sum());
        for (var metamodel : internal.getMetamodels()) {
            for (var il : internal.getConnectionState(metamodel).getInstanceLinks()) {
                var ri = il.getFirstEndpoint();
                System.out.printf("  instance link %s: RI \"%s\" (sentences %s) -> %s, similar=%s%n", metamodel, ri.getName(),
                        ri.getNameMappings().flatCollect(nm -> nm.getWords()).collect(w -> w.getSentenceNumber() + 1).toSortedSet(), il.getSecondEndpoint().getName(),
                        SimilarityUtils.getInstance().isRecommendedInstanceSimilarToModelInstance(ri, il.getSecondEndpoint()));
            }
        }

        for (var mode : MODES) {
            var self = runExternal("self-" + mode, text, pcm, new File(out, "self-" + mode), selfLinks, mode);
            var selfFindings = report(self, raw, modelXml);
            System.out.println("  round-trip equals internal: " + selfFindings.equals(internalFindings) + diff(internalFindings, selfFindings));
        }
        if (gold != null) {
            Map<Integer, Set<String>> goldLinks = new TreeMap<>();
            Files.readAllLines(gold.toPath()).stream().skip(1).filter(l -> !l.isBlank()).forEach(l -> {
                var p = l.split(",");
                goldLinks.computeIfAbsent(Integer.parseInt(p[1].trim()), k -> new TreeSet<>()).add(p[0].trim());
            });
            System.out.println("Gold SAD-SAM links: " + goldLinks.values().stream().mapToInt(Set::size).sum());
            for (var mode : MODES) {
                report(runExternal("gold-" + mode, text, pcm, new File(out, "gold-" + mode), goldLinks, mode), raw, modelXml);
            }
        }
    }

    /** Same steps as InconsistencyDetection#definePipeline, plus a step that replaces the computed links with the given ones. */
    /**
     * How a sentence-level link (sentence s, element m) becomes RecommendedInstance->m links, for RIs with a word in s:
     * similar = RI name similar to m; sentence = every RI; internal+similar = ArDoCo's own instance link RI->m exists, or similar.
     */
    static final List<String> MODES = List.of("similar", "sentence", "internal+similar");

    static ArdocoResult runExternal(String name, File text, File pcm, File out, Map<Integer, Set<String>> links, String mode) {
        out.mkdirs();
        long t0 = System.nanoTime();
        var ardoco = new Ardoco(name);
        var repo = ardoco.getDataRepository();
        DataRepositoryHelper.putInputText(repo, readText(text));
        ardoco.addPipelineStep(TextPreprocessingAgent.get(NO_CONFIG, repo));
        ardoco.addPipelineStep(ModelProviderAgent.getModelProviderAgent(repo, NO_CONFIG,
                new ArchitectureConfiguration(pcm, format(pcm), Metamodel.ARCHITECTURE_WITH_COMPONENTS_AND_INTERFACES), null));
        ardoco.addPipelineStep(TextExtraction.get(NO_CONFIG, repo));
        ardoco.addPipelineStep(RecommendationGenerator.get(NO_CONFIG, repo));
        ardoco.addPipelineStep(ConnectionGenerator.get(NO_CONFIG, repo)); // kept: it also adds RecommendedInstances
        ardoco.addPipelineStep(new ExternalLinks(repo, links, mode));
        ardoco.addPipelineStep(InconsistencyChecker.get(NO_CONFIG, repo));
        var result = ardoco.runAndSave(out);
        System.out.printf("RUN %s (external links, RI mapping=%s): %.1f s%n", name, mode, (System.nanoTime() - t0) / 1e9);
        return result;
    }

    /**
     * Replaces ConnectionStates with RecommendedInstance->ModelEntity links derived from sentence-level links.
     * ponytail: RI selection is a heuristic (see MODES); an upstream API taking sentence-level links would avoid it.
     */
    static final class ExternalLinks extends AbstractPipelineStep implements Claimant {
        private final Map<Integer, Set<String>> links;
        private final String mode;

        ExternalLinks(DataRepository repo, Map<Integer, Set<String>> links, String mode) {
            super("ExternalLinks", repo);
            this.links = links;
            this.mode = mode;
        }

        @Override
        protected void process() {
            var repo = getDataRepository();
            var models = DataRepositoryHelper.getModelStatesData(repo);
            var recommendations = DataRepositoryHelper.getRecommendationStates(repo);
            var computed = DataRepositoryHelper.getConnectionStates(repo);
            var fresh = ConnectionStatesImpl.build(models.getMetamodels().toArray(Metamodel[]::new));
            int added = 0;
            for (var metamodel : models.getMetamodels()) {
                var state = fresh.getConnectionState(metamodel);
                var entities = models.getModel(metamodel).getEndpoints();
                var internalLinks = computed.getConnectionState(metamodel).getInstanceLinks();
                for (var ri : recommendations.getRecommendationState(metamodel).getRecommendedInstances()) {
                    Set<Integer> riSentences = ri.getNameMappings().flatCollect(nm -> nm.getWords()).collect(w -> w.getSentenceNumber() + 1).toSet();
                    for (var entity : entities) {
                        boolean linked = riSentences.stream().anyMatch(s -> links.getOrDefault(s, Set.of()).contains(entity.getId()));
                        boolean similar = SimilarityUtils.getInstance().isRecommendedInstanceSimilarToModelInstance(ri, entity);
                        boolean accept = switch (mode) {
                        case "similar" -> similar;
                        case "sentence" -> true;
                        default -> similar || internalLinks.anySatisfy(il -> il.getFirstEndpoint().equals(ri) && il.getSecondEndpoint().equals(entity));
                        };
                        if (linked && accept) {
                            state.addToLinks(ri, entity, this, 1.0);
                            added++;
                        }
                    }
                }
            }
            repo.addData(ConnectionStates.ID, fresh);
            System.out.println("  ExternalLinks: RI->model links injected: " + added);
        }

        @Override
        protected void before() {
        }

        @Override
        protected void after() {
        }

        @Override
        protected void delegateApplyConfigurationToInternalObjects(ImmutableSortedMap<String, String> map) {
        }
    }

    // ---------------------------------------------------------------- reporting

    /** Prints counts/examples and returns a comparable set of finding keys. */
    static Set<String> report(ArdocoResult result, String raw, String modelXml) throws Exception {
        var all = result.getAllInconsistencies();
        Map<String, Long> perType = new TreeMap<>(all.castToList().stream().collect(Collectors.groupingBy(Inconsistency::getType, Collectors.counting())));
        System.out.println("  findings per type: " + perType + " (total " + all.size() + ")");
        Set<String> keys = new TreeSet<>();
        var sentences = result.getText().getSentences();
        for (var inc : all) {
            if (inc instanceof ModelInconsistency mi) {
                var id = mi.getModelInstanceUid();
                keys.add(inc.getType() + " " + id);
                System.out.printf("    %s id=%s idInModelFile=%s | %s%n", inc.getType(), id, modelXml.contains("id=\"" + id + "\""), inc.getReason());
            } else if (inc instanceof TextInconsistency ti) {
                int n = ti.getSentenceNumber();
                var s = sentences.detect(x -> x.getSentenceNumber() + 1 == n);
                keys.add(inc.getType() + " S" + n + " " + inc.toFileOutput().getFirst()[2]);
                System.out.printf("    %s sentence(1-based)=%d hash=%s | %s | \"%s\"%n", inc.getType(), n, hash12(s.getText()), inc.getReason(), s.getText());
            }
        }
        return keys;
    }

    static String diff(Set<String> a, Set<String> b) {
        var onlyA = new TreeSet<>(a);
        onlyA.removeAll(b);
        var onlyB = new TreeSet<>(b);
        onlyB.removeAll(a);
        return onlyA.isEmpty() && onlyB.isEmpty() ? "" : " | only internal: " + onlyA + " | only round-trip: " + onlyB;
    }

    static ModelFormat format(File model) {
        return model.getName().endsWith(".uml") ? ModelFormat.UML : ModelFormat.PCM;
    }

    static String readText(File f) {
        try {
            return Files.readString(f.toPath());
        } catch (Exception e) {
            throw new IllegalStateException(e);
        }
    }
}
