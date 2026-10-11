package edu.kit.kastel.mcse.ardoco.sandman.tlr.ardoco;

import com.fasterxml.jackson.datatype.jdk8.Jdk8Module;
import edu.kit.kastel.mcse.ardoco.core.api.PreprocessingData;
import edu.kit.kastel.mcse.ardoco.core.api.models.CodeModel;
import edu.kit.kastel.mcse.ardoco.core.api.models.CodeModelWithCompilationUnitsAndPackages;
import edu.kit.kastel.mcse.ardoco.core.api.models.Metamodel;
import edu.kit.kastel.mcse.ardoco.core.api.models.ModelStates;
import edu.kit.kastel.mcse.ardoco.core.api.models.code.CodeCompilationUnit;
import edu.kit.kastel.mcse.ardoco.core.api.models.code.ProgrammingLanguage;
import edu.kit.kastel.mcse.ardoco.core.api.output.ArdocoResult;
import edu.kit.kastel.mcse.ardoco.core.api.text.Text;
import edu.kit.kastel.mcse.ardoco.core.common.IdentifierProvider;
import edu.kit.kastel.mcse.ardoco.core.common.JsonHandling;
import edu.kit.kastel.mcse.ardoco.core.common.util.CommonUtilities;
import edu.kit.kastel.mcse.ardoco.core.common.util.DataRepositoryHelper;
import edu.kit.kastel.mcse.ardoco.core.data.DataRepository;
import edu.kit.kastel.mcse.ardoco.core.execution.Ardoco;
import edu.kit.kastel.mcse.ardoco.core.execution.runner.ArdocoRunner;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.CandidateLink;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.KindPair;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.ScopeMode;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.TlrContext;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.TlrInput;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.TraceLinkApproach;
import edu.kit.kastel.mcse.ardoco.tlr.codetraceability.SadCodeTraceabilityLinkRecovery;
import edu.kit.kastel.mcse.ardoco.tlr.connectiongenerator.ConnectionGenerator;
import edu.kit.kastel.mcse.ardoco.tlr.recommendationgenerator.RecommendationGenerator;
import edu.kit.kastel.mcse.ardoco.tlr.text.providers.TextPreprocessingAgent;
import edu.kit.kastel.mcse.ardoco.tlr.textextraction.TextExtraction;
import java.io.File;
import java.io.IOException;
import java.io.UncheckedIOException;
import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Collection;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeSet;
import java.util.concurrent.CancellationException;
import org.eclipse.collections.api.factory.SortedMaps;

/**
 * ArDoCo SAD to code (approach {@code ardocode}, T2.1b): the sentences of each current architecture document against
 * the ArDoCo code model that the plugin {@code code-extractor-ardoco} builds from the current code. ArDoCo links
 * sentences to compilation units, so every target is a file. The ACM is fetched once per call, the annotation comes
 * from the CoreNLP provider service like in {@link ArdocoApproach}.
 */
public final class ArdocodeApproach implements TraceLinkApproach {

    public static final String ID = "ardocode";
    /** Raise the +aN suffix when the mapping of ArDoCo results to links changes. */
    public static final String VERSION = "2.1.0+a1";

    private final CorenlpClient corenlp;
    private final CodeModelClient codeModel;

    /** ServiceLoader entry: settings from the environment, read when the first run needs them. */
    public ArdocodeApproach() {
        this(null, null);
    }

    ArdocodeApproach(CorenlpClient corenlp, CodeModelClient codeModel) {
        this.corenlp = corenlp;
        this.codeModel = codeModel;
    }

    private CorenlpClient corenlp() {
        return corenlp != null ? corenlp : CorenlpClient.fromEnv();
    }

    private CodeModelClient codeModel() {
        return codeModel != null ? codeModel : CodeModelClient.fromEnv();
    }

    @Override
    public String id() {
        return ID;
    }

    @Override
    public String version() {
        return VERSION;
    }

    @Override
    public Set<KindPair> supports() {
        return Set.of(new KindPair("architecture-doc", "code"));
    }

    @Override
    public ScopeMode scopeMode() {
        return ScopeMode.WHOLE_PROJECT;
    }

    @Override
    public Set<String> paramKeys() {
        return Set.of();
    }

    /**
     * The CoreNLP provider and the plugin (name and version) enter configHash. The ACM does not: it is a function of
     * the current code versions (the Run's USED) and the plugin version.
     */
    @Override
    public String environment(TlrContext ctx) {
        String provider;
        try {
            provider = corenlp().provider(ctx.cancelled()).identity();
        } catch (RuntimeException e) {
            provider = "provider=unavailable";
        }
        String plugin;
        try {
            plugin = codeModel().identity();
        } catch (RuntimeException e) {
            plugin = "unavailable";
        }
        return provider + ";codeModel=" + plugin;
    }

    @Override
    public List<CandidateLink> recover(TlrInput in, TlrContext ctx) {
        List<TlrInput.Version> docs = in.versions().stream()
                .filter(v -> "architecture-doc".equals(v.kind()))
                .sorted(Comparator.comparing(TlrInput.Version::id))
                .toList();
        List<TlrInput.Version> code = in.versions().stream()
                .filter(v -> "code".equals(v.kind()))
                .sorted(Comparator.comparing(TlrInput.Version::path))
                .toList();
        Map<String, CandidateLink> links = new LinkedHashMap<>();
        if (docs.isEmpty() || code.isEmpty()) {
            return new ArrayList<>(links.values());
        }
        Map<String, TlrInput.Element> files = new LinkedHashMap<>();
        for (TlrInput.Element t : in.targets()) {
            if ("file".equals(t.kind()) && "CodeUnit".equals(t.label()) && files.put(t.localKey(), t) != null) {
                throw new IllegalStateException("two file elements with local key " + t.localKey());
            }
        }
        if (ctx.artifacts() == null) {
            throw new IllegalStateException("no artifact store configured, cannot read the code");
        }
        CorenlpClient annotator = corenlp();
        CorenlpClient.Provider provider = null;
        Path dir = null;
        try {
            dir = Files.createTempDirectory("ardocode-");
            Path acmFile = null;
            for (TlrInput.Version doc : docs) {
                List<TlrInput.Element> sentences = SentenceRendering.sentences(in.sources(), doc.id());
                if (sentences.isEmpty()) {
                    ctx.log().warn("ardocode: document version {} has no sentences, skipped", doc.id());
                    continue;
                }
                if (ctx.cancelled().getAsBoolean()) {
                    throw new CancellationException("cancelled");
                }
                if (acmFile == null) {
                    acmFile = fetchAcm(in, code, ctx, dir);
                }
                SentenceRendering.assertHashes(sentences);
                if (provider == null) {
                    provider = annotator.provider(ctx.cancelled());
                    ctx.log().info("ardocode: CoreNLP {}", provider.identity());
                }
                Text annotated = TextCache.annotate(annotator, provider, SentenceRendering.lines(sentences), ctx);
                run(ctx, dir, acmFile, sentences, annotated, files)
                        .forEach(l -> links.put(l.sourceId() + ">" + l.targetId(), l));
            }
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        } finally {
            if (dir != null) {
                ArdocoRuns.delete(dir, ctx);
            }
        }
        return new ArrayList<>(links.values());
    }

    private Path fetchAcm(TlrInput in, List<TlrInput.Version> code, TlrContext ctx, Path dir) throws IOException {
        List<CodeModelClient.File> request = code.stream()
                .map(v -> new CodeModelClient.File(
                        v.path(), ctx.artifacts().pathFor(v.storageUri()).toString()))
                .toList();
        long start = System.nanoTime();
        var acm = codeModel().acm(in.projectId(), request, ctx.cancelled());
        Path file = dir.resolve("codeModel.acm");
        String sha = CodeModelClient.write(acm, file);
        ctx.log()
                .info(
                        "ardocode: ACM of {} files, {} bytes, sha256 {}, plugin call {} ms",
                        request.size(),
                        Files.size(file),
                        sha,
                        (System.nanoTime() - start) / 1_000_000);
        return file;
    }

    private List<CandidateLink> run(
            TlrContext ctx,
            Path dir,
            Path acmFile,
            List<TlrInput.Element> sentences,
            Text annotated,
            Map<String, TlrInput.Element> files)
            throws IOException {
        Path run = Files.createTempDirectory(dir, "run-");
        Path textFile = run.resolve("text.txt");
        Files.writeString(textFile, SentenceRendering.text(SentenceRendering.lines(sentences)), StandardCharsets.UTF_8);
        ArdocoRuns.acquire(ctx);
        try {
            long start = System.nanoTime();
            ArdocoRuns.resetHeapPeak();
            var runner = new SadCodeRunner();
            runner.setUp(textFile.toFile(), acmFile.toFile());
            DataRepositoryHelper.putPreprocessingData(
                    runner.getArdoco().getDataRepository(), new PreprocessingData(annotated));
            var repository = runner.runWithoutSaving();
            if (repository == null) {
                throw new IllegalStateException("ArDoCo run did not start");
            }
            List<Map.Entry<Integer, String>> pairs = new ArrayList<>();
            for (var tl : new ArdocoResult(repository).getSadCodeTraceLinks()) {
                pairs.add(
                        Map.entry(tl.getFirstEndpoint().getSentence().getSentenceNumber(), pathOf((CodeCompilationUnit)
                                tl.getSecondEndpoint())));
            }
            List<CandidateLink> links = toLinks(pairs, sentences, files);
            ctx.log()
                    .info(
                            "ardocode run: {} sentences, {} links, {} ms, peak heap {} MiB",
                            sentences.size(),
                            links.size(),
                            (System.nanoTime() - start) / 1_000_000,
                            ArdocoRuns.peakHeapMiB());
            return links;
        } finally {
            ArdocoRuns.LOCK.unlock();
        }
    }

    /**
     * ArDoCo's {@code Ardocode} runner without its model provider step. The runner reads the ACM with
     * {@code CodeExtractor.readInCodeModel}, and the class initializer of {@code CodeExtractor} loads Magika and
     * onnxruntime, which this module does not have (T2.1b deviation). This reads the ACM like
     * {@code ModelProviderInformant} does (Jackson, then {@code IdentifierProvider.reset}) and adds the same steps as
     * {@code Ardocode.definePipeline} for ArDoCo 2.1.0. The benchmark test compares it with the real runner.
     */
    private static final class SadCodeRunner extends ArdocoRunner {

        SadCodeRunner() {
            super("sandman");
        }

        void setUp(File textFile, File acm) throws IOException {
            Ardoco ardoco = getArdoco();
            DataRepository repository = ardoco.getDataRepository();
            String text = CommonUtilities.readInputText(textFile);
            if (text.isBlank()) {
                throw new IllegalArgumentException("Cannot deal with empty input text");
            }
            DataRepositoryHelper.putInputText(repository, text);
            var mapper = JsonHandling.createObjectMapper();
            mapper.registerModule(new Jdk8Module());
            CodeModel model =
                    new CodeModelWithCompilationUnitsAndPackages(mapper.readValue(acm, CodeModel.CodeModelDto.class));
            var states = new ModelStates();
            states.addModel(Metamodel.CODE_WITH_COMPILATION_UNITS_AND_PACKAGES, model);
            repository.addData(ModelStates.ID, states);
            IdentifierProvider.reset();
            var configs = SortedMaps.immutable.<String, String>empty();
            ardoco.addPipelineStep(TextPreprocessingAgent.get(configs, repository));
            ardoco.addPipelineStep(TextExtraction.get(configs, repository));
            ardoco.addPipelineStep(RecommendationGenerator.get(configs, repository));
            ardoco.addPipelineStep(ConnectionGenerator.get(configs, repository));
            ardoco.addPipelineStep(SadCodeTraceabilityLinkRecovery.get(configs, repository));
            isSetUp = true;
        }
    }

    /** One link per distinct (sentence number, file); a path or sentence that is not known fails the run. */
    static List<CandidateLink> toLinks(
            Collection<Map.Entry<Integer, String>> pairs,
            List<TlrInput.Element> sentences,
            Map<String, TlrInput.Element> files) {
        Set<Map.Entry<Integer, String>> distinct =
                new TreeSet<>(Map.Entry.<Integer, String>comparingByKey().thenComparing(Map.Entry.comparingByValue()));
        distinct.addAll(pairs);
        List<CandidateLink> links = new ArrayList<>();
        for (var pair : distinct) {
            TlrInput.Element target = files.get(pair.getValue());
            if (target == null || pair.getKey() < 0 || pair.getKey() >= sentences.size()) {
                throw new IllegalStateException(
                        "ArDoCo link to unknown file " + pair.getValue() + " or sentence " + pair.getKey());
            }
            links.add(new CandidateLink(sentences.get(pair.getKey()).id(), target.id(), "documents", 1.0, null));
        }
        return links;
    }

    /**
     * Project path of a compilation unit, as {@code ElementMapping.pathOf} in code-extractor-ardoco (same cases in
     * the tests): ArDoCo 2.1.0 percent-encodes Java paths whole but shell paths only up to the file name.
     */
    static String pathOf(CodeCompilationUnit unit) {
        String path = unit.getPath();
        if (unit.getLanguage() == ProgrammingLanguage.JAVA) {
            return decode(path);
        }
        int name = path.lastIndexOf('/') + 1;
        return decode(path.substring(0, name)) + path.substring(name);
    }

    static String decode(String encoded) {
        try {
            return URLDecoder.decode(encoded.replace("+", "%2B"), StandardCharsets.UTF_8);
        } catch (IllegalArgumentException e) {
            return encoded;
        }
    }
}
