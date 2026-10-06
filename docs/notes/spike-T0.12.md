# Spike T0.12 — ArDoCo inconsistency detection (V-16, V-17, V-27)

Checked on 2026-10-06 against ArDoCo `feature/make-inconsistency-great-again` @ `0d1f22b11caf3a758e5d32e5d3b06020c15d7407`
(still the branch head on that day) and `named-architecture-entity-recognition` (NAER) `main` @ `d631a7085b0c97909e1edcf03f651048e8718170`.
Temurin JDK 21.0.12, Maven 3.9.16, macOS arm64. Programs and commands: `spikes/T0.12/` (README). Paths below without a prefix are
relative to the ArDoCo checkout; `Spike.java` = `spikes/T0.12/src/main/java/Spike.java`.

Benchmark: MediaStore from the subtree `core/tests-base/src/main/resources/benchmark/mediastore` (37 sentences, PCM `ms.repository`,
UML `ms.uml`, gold standards). Only the non-LLM `InconsistencyDetection` was run. The Artemis variant was analysed from source only.

## Summary

| # | Question | Answer | Evidence | Consequence for the design |
|---|---|---|---|---|
| 1 | V-17 build | Does **not** build standalone. `core/framework/common` needs `io.github.ardoco:named-architecture-entity-recognition:2.1.0-SNAPSHOT`, which is published nowhere, and NAER `main` in turn needs `io.github.ardoco.core:common:2.1.0-SNAPSHOT` (a cycle across two repos). Two-pass bootstrap with both commits pinned works: `BUILD SUCCESS`, 50 s for the full ArDoCo build, 116 s for the whole script from scratch, 753 MB Maven repo. JDK 26 also builds (31 s, `release 21`). | §1 | B1 needs **two** pinned commits and the bootstrap order in the Docker stage and `make ardoco` (copy `spikes/T0.12/bootstrap.sh`). Ask upstream to publish NAER snapshots or to remove the cycle. Image grows by about 570 MB (CoreNLP models 464 MB, onnxruntime 96 MB). |
| 2 | V-17 API | `InconsistencyDetection.setUp(File text, File model, ModelFormat, configs, File outDir)`, then `run()` (returns `ArdocoResult` and writes 3 files) or `runWithoutSaving()` (returns the `DataRepository`). Accepts any path. Outputs are `ModelEntityAbsentFromTextInconsistency` (model id via `getModelInstanceUid()`) and `TextEntityAbsentFromModelInconsistency` (word, 1-based sentence, confidence). Artemis: `ArtemisInconsistencyDetection.setUp(text, ArchitectureConfiguration?, CodeConfiguration?, configs, LargeLanguageModel, strategies)`. It uses the same two types, stored in a separate `ArtemisInconsistencyStates` entry that `ArdocoResult` does not expose. The LLM is a LangChain4j `ChatModel` (non-streaming), configured only through process-wide env vars or a `.env` file. | §2 | The T3.1 adapter can stage inputs from the artifact store as temp files and use `runWithoutSaving()`. It assembles its own pipeline from the public `X.get(config, repo)` factories, because `definePipeline` is private. For Artemis it reads `ArtemisInconsistencyStates` from the `DataRepository`. V-29: no per-call or per-project base URL or key, only a gateway route via `OLLAMA_GENERIC` + `OLLAMA_TOKEN`. An upstream change to accept a `ChatModel` is needed. |
| 3 | Run on MediaStore | 42 findings: 6 `ModelEntityAbsentFromText` + 36 `TextEntityAbsentFromModel` in 17 sentences. The UML model gives the same counts. 4.2–5.6 s for the first run in a JVM (includes loading CoreNLP), about 2 s for later runs. Peak RSS 1.08 GB for 3 runs in one JVM. Output was the same across repeated runs. | §3 | Text findings are per *word*, and some differ only in confidence (S1 "Media" 0.63 and 0.65). The adapter must deduplicate by (sentence, word) before fingerprinting. 2 of the 6 model findings are false positives against the gold standard (DB, FileStorage). |
| 4a | V-16 sentences | CoreNLP `ssplit`. Sentences are numbered internally by a 0-based contiguous index. Findings, output files and gold standards use 1-based numbers. `Sentence.getText()` is the raw substring of the input (no token artefacts, original whitespace and newlines kept): 1,467 of 1,467 sentences across 10 benchmark texts and a Markdown fixture are verbatim. Identical sentences occur in 5 of 10 texts (coronawarnapp has one text 18 times). Plain CoreNLP does **not** split at newlines or blank lines, so a Markdown heading merges with the next sentence, and so does a list item without a period. The split settings are hard-coded. **Verified workaround:** annotate a one-sentence-per-line rendering yourself (same properties + `ssplit.eolonly=true`) and inject it as `PreprocessingData`. ArDoCo then skips its own NLP. Sentence *i* = line *i*, and MediaStore output is byte-identical to the standard runner. | §4 | Proposed change to ADR-0015 S2: the adapter renders the version's `TextUnit` sentences one per line (normalized text, no blank lines) and maps ArDoCo sentence *n* to the (*n*−1)-th rendered sentence. The mapping is then exact by construction, and the hash is only an assertion. T1.1 does not need to imitate CoreNLP (RK-3 ↓). This also settles which text ArDoCo sees (R-025). Duplicates need the occurrence counter (already in ADR-0015). |
| 4b | V-16 model ids | `ModelEntity.getId()` = PCM `id` or UML `xmi:id`. All 23 MediaStore endpoints (components **and interfaces**) are found in the model file, for PCM and UML alike. Benchmark UML ids equal PCM ids, and gold standards use the same ids. | §4 | `ModelElement.localKey` = XMI id maps exactly. The extractor must also emit interfaces, because ArDoCo links text to interfaces (e.g. "download" → `IDownload`). |
| 5 | V-27 external links | There is no setter or option for external links. Both detectors read `ConnectionState.getInstanceLinks()`, which are *RecommendedInstance → ModelEntity* links, not sentence links. **Without an upstream change** a custom pipeline step after `ConnectionGenerator` can replace `ConnectionStates` (`ConnectionStatesImpl.build` + `addToLinks`, `DataRepository.addData` overwrites). This was verified. Sentence-level links must then be turned back into instance links by a heuristic. Feeding back ArDoCo's own links is exact only with the mapping "existing ArDoCo instance link or name-similar" (42 = 42 findings). No mapping gets both types right for links of other approaches (gold: `similar` → 7 model findings vs 4 in the gold UME, `sentence` → exactly the 4 but 0 text findings). | §5 | Q-20/L2 can start with the workaround (mapping `internal+similar`, equivalence for `linkApproaches: [ardoco]` holds on MediaStore). For exact model findings, contribute the upstream change sketched in §5 (an optional data entry with sentence-level links, read by the two informants) or run the checker twice (not tried). Text findings with links from other approaches stay heuristic either way. RK-18 likelihood stays M (semantics, not feasibility). ADR-0015 keeps V-27 as an open point with this result. |
| 6 | Dependencies | LangChain4j 1.18.1 (`langchain4j`, `-open-ai`, `-ollama`, `-http-client-jdk`) comes in through `model-provider` even on the non-LLM path. Also: CoreNLP 4.5.10 (protobuf-java 3.25.5, `jakarta.servlet-api` 4.0.4, javax JAXB, xalan/xerces/xom), Eclipse JDT 3.46 + Equinox/OSGi + JNA 5.18.1, onnxruntime 1.20.0 (magika), jgit 7.7.1, opennlp 2.5.11, Jackson 2.22.2, SLF4J 2.0.18, httpclient5 5.6.3, dotenv-java. No Netty. The Quarkus 3.33.4 BOM pins different versions: Jackson 2.21.7 (lower), protobuf-java 4.33.2 (major up), servlet-api 6.0.0, JNA 5.8.0 (lower), httpclient5 5.5.2 (lower), SLF4J 2.0.17. | §6 | Expect BOM-driven downgrades. The likely risks are protobuf 3→4 for CoreNLP and JNA for the Eclipse platform. Neither is used on the local annotation path, but this is **not verified**. Run the T3.3 equivalence test on the agent's Quarkus classpath. LangChain4j is not managed by the core Quarkus BOM, so align it with LiSSA (T2.2). Do not put a `.env` in the agent's working directory, and set `LLM_CACHE_DIR`. |

## 1. Build (V-17)

```
$ mvn -B install -DskipTests            # pinned commit, JDK 21, empty Maven repo
[ERROR] Failed to execute goal on project common: Could not resolve dependencies ...
[ERROR] dependency: io.github.ardoco:named-architecture-entity-recognition:jar:2.1.0-SNAPSHOT (compile)
[ERROR] 	Could not find artifact ... in mavenCentral / in mavenSnapshot (https://central.sonatype.com/repository/maven-snapshots/)
```

* The dependency is declared in `core/framework/common/pom.xml:31-35` (comment: "2.1.0-SNAPSHOT für weiterentwicklung sonst 2.0.0").
  Central only has NAER 2.0.0, and the Sonatype snapshot metadata returns 404.
* NAER `main` (`pom.xml`) has parent `io.github.ardoco:parent:2.1.0-SNAPSHOT` and depends on `io.github.ardoco.core:common:2.1.0-SNAPSHOT`.
  ArDoCo `main` (`1c9c186`) has no NAER dependency in `common`, so the published snapshot `common-2.1.0-20260821.142130-1` (built from
  `main`) breaks the cycle. That snapshot was the only one downloaded from the snapshot repository.
* Bootstrap (`spikes/T0.12/bootstrap.sh`): branch parent `-N install` → NAER against the published `common` → full ArDoCo build → NAER and
  ArDoCo again offline against the branch's `common`. Every step returned `BUILD SUCCESS`. Run from scratch with an isolated Maven repo:
  `real 115.54` s, full ArDoCo build `Total time: 50.603 s`, offline rebuild 21.8 s, `m2` 753 MB.
* JDK 26 (`temurin-26`, offline): `BUILD SUCCESS`, 31.4 s.
* Needed artifacts: `io.github.ardoco.id:pipeline-id` (both runners) pulls `inconsistency-detection`, `artemis-inconsistency-detection`,
  `connection-generator`, `text-extraction`, `recommendation-generator`, `text-preprocessing`, `model-provider`, `artemis`,
  `pipeline-core`, `common`, `text-provider-json` and NAER. The runtime classpath has 114 jars and about 650 MB including test scope.
  The largest jars are `stanford-corenlp-4.5.10-models.jar` (464 MB) and `onnxruntime-1.20.0.jar` (96 MB).

## 2. API (V-17)

* `inconsistency-detection/pipeline-id/.../runner/InconsistencyDetection.java:27-66`: `setUp(File inputText, File inputModelArchitecture,
  ModelFormat {PCM, UML, COMPONENT_LISTING, ACM}, ImmutableSortedMap<String,String> additionalConfigs, File outputDir)`. The text is read
  whole as UTF-8. The metamodel is fixed to `ARCHITECTURE_WITH_COMPONENTS_AND_INTERFACES`. Pipeline (private `definePipeline`, :47):
  `TextPreprocessingAgent` (CoreNLP) → `ModelProviderAgent` → `TextExtraction` → `RecommendationGenerator` → `ConnectionGenerator` →
  `InconsistencyChecker`. The spike rebuilds the same pipeline with `new Ardoco(name)` and the public static `get(...)` factories
  (`Spike.runExternal`).
* `core/pipeline-core/.../runner/ArdocoRunner.java:44` `run()` needs an output directory and writes `inconsistencyDetection_<name>.txt`,
  `sadSamTlr_<name>.csv` and `traceLinks_<name>.txt`. `:58` `runWithoutSaving()` returns the `DataRepository`, and
  `new ArdocoResult(repo)` (public record) gives `getAllInconsistencies()`.
* CoreNLP runs locally by default. `NLP_PROVIDER_SOURCE=microservice` + `MICROSERVICE_URL` switch to a remote service
  (`text-preprocessing/.../corenlp/config/ConfigManager.java:40-45`, `TextProcessor.java:30`).
* Output types (`inconsistency-detection/stages-id/inconsistency-detection/.../types/`):
  * `ModelEntityAbsentFromTextInconsistency`: `getModelInstanceUid()` = `ModelEntity.getId()` (:67). It is created for endpoints of the
    configurable `types` (default `Component`, `BasicComponent`, `CompositeComponent`), excluding those with at least
    `minimumNeededTraceLinks` (1) instance links and those on the `whitelist`
    (`ModelEntityAbsentFromTextInconsistencyInformant.java:29-34, 87-98`).
  * `TextEntityAbsentFromModelInconsistency` record `(String name, int sentence, double confidence, MissingElementInconsistencyCandidate
    origin)`. It covers RecommendedInstances without an instance link that share no word with a linked RI. **One finding per word**, with
    `sentence = word.getSentenceNumber() + 1` (`TextEntityAbsentFromModelInconsistencyInformant.java:55-60, 121-124`).
* Artemis (`ArtemisInconsistencyDetection.java:31-70`): `setUp(File text, ArchitectureConfiguration?, CodeConfiguration? (ACM file or
  directory), configs, [File outDir], LargeLanguageModel llm, List<ArtemisNerStrategy>)` with the strategies `Component`, `Class` and
  `Datafile`. The text goes through `SimpleTextPreprocessingAgent`, which is line-based without CoreNLP: it splits on `\R`, trims, drops
  blank lines and appends `.` when there is no `[.!?]` at the end (`SimpleTextImpl.java:31-38`). NER runs through NAER. Occurrence
  numbers are 1-based line numbers (NAER `NamedEntity.java:129-133`).
  * MEAT: every endpoint of the strategy's metamodel without an Artemis trace link, no type filter
    (`ArtemisMeatInconsistencyInformant.java:29-36`).
  * TEAM: every occurrence of an unlinked named entity, confidence fixed at 0.92 (`ArtemisTeamInconsistencyInformant.java:20-22`).
  * Results land in `ArtemisInconsistencyStates` (the `ArtemisInconsistencyInformant.java:32` TODO notes that `ArdocoResult` does not
    expose them).
* LLM (`tlr/stages-tlr/model-provider/.../informants/LargeLanguageModel.java`): an enum of LangChain4j `ChatModel` suppliers wrapped in
  `CachedChatLanguageModel`, a JSON file cache in `LLM_CACHE_DIR` (default `./.cache-llm/`). The OpenAI entries need `OPENAI_API_KEY` +
  `OPENAI_ORGANIZATION_ID`, and their base URL cannot be set (:99-110). `OLLAMA_GENERIC` uses `OLLAMA_HOST` / `OLLAMA_MODEL_NAME`, and
  with `OLLAMA_TOKEN` it builds an OpenAI-compatible client `baseUrl(OLLAMA_HOST).apiKey(token)` (:114-125). All values come from
  `Environment.getEnv`, which reads `.env` in the working directory first, then the system environment
  (`core/.../util/Environment.java:100`). `SEED` defaults to 422413373. The calls are non-streaming (`ChatModel`).

## 3. Run on MediaStore

`run detect …/ms.repository …` (README). Excerpt:

```
RUN internal (standard InconsistencyDetection runner): 4,2 s
  findings per type: {ModelEntityAbsentFromText=6, TextEntityAbsentFromModel=36} (total 42)
    ModelEntityAbsentFromText id=_S-lawHDqEeSqnN80MQ2uGw idInModelFile=true | Model contains an Instance "AudioWatermarking" (type: "BasicComponent") ...
    ModelEntityAbsentFromText id=_5LN7MLg2EeSNPorBlo7x9g idInModelFile=true | ... "DB" ...
    (also FileStorage, Cache, DownloadLoadBalancer, ParallelWatermarking)
    TextEntityAbsentFromModel sentence(1-based)=12 hash=f583015f65d2 | Text indicates that "database" should be contained in the model(s) ... (confidence: 0.84) | "The UserDBAdapter component queries the database."
    TextEntityAbsentFromModel sentence(1-based)=23 hash=ffd88b7e89eb | ... "Database" ... | "The Database component represents an actual database (e.g., MySQL)."
MODEL ARCHITECTURE_WITH_COMPONENTS_AND_INTERFACES: endpoints=23, ids not found as id="..." in model file: [], types=[?, BasicComponent]
```

* Text findings by word: database 7, data 6, Database 5, component 4, access 4, Media 4, Store 2, it 2, It 1, bit 1. They fall in the
  sentences 1, 9, 12, 13, 20, 22–25, 27–29, 31–35.
* Gold UME (`goldstandard_sad_2016-sam_2016_UME.csv`): AudioWatermarking, Cache, DownloadLoadBalancer, ParallelWatermarking. DB and
  FileStorage are false positives because ArDoCo does not link "Database" and "DataStorage".
* UML (`ms.uml`): same 6 + 36. With a pre-split text (§4) the inconsistency and TLR output files are identical to the standard run apart
  from the timestamp header (`diff` empty).
* `/usr/bin/time -l` for one JVM with 3 pipeline runs: `8.39 real`, `1081393152 maximum resident set size`.

## 4. Sentence and model-element mapping (V-16)

* Numbering: `TextImpl.java:63-65` creates `SentenceImpl(coreSentence, i, …)` for `i = 0..n-1`. `ArdocoResult.java:56` and
  `TextEntityAbsentFromModelInconsistencyInformant.java:122` add 1. The gold standards are 1-based (`modelElementID,sentence`).
* Text: `SentenceImpl.java:44` `this.text = coreSentence.text()`, the original character span.
  `run sentences fixtures/wrapped.md $B/*/text_*/*.txt` checks every sentence against the raw file and computes the key hash
  (`Spike.hash12` = first 12 hex of sha256 of the NFC text with ASCII whitespace collapsed; self-checked against `vectors.json`):

```
SENTENCES mediastore.txt: count=37, rawLines=37, ..., verbatim substring of raw=37, containing newline=0, distinct hashes=37, collisions=[]
SENTENCES coronawarnapp.txt: count=420, rawLines=370, ..., verbatim=420, containing newline=1, distinct hashes=391, collisions=[bb7208bc9b5d=[7, 44, 90, … 18 entries], c977dbe78522=[… 7], 6bc54f13bea5=[… 6], 267c23695b6e=[22, 24]]
SENTENCES scons.txt: count=297, ..., distinct hashes=284, collisions=[8 groups]
SENTENCES beets.txt: count=131, rawLines=125, ..., collisions=[cdb4ee2aea69=[5, 26, 44, 72]]   ← sentence "."
SENTENCES rod.txt / teammates.txt: 1 collision group each; bigbluebutton, jabref, teastore, zengarden, mediastore: none
SENTENCES wrapped.md: count=10, rawLines=15, ..., containing newline=3
  S000 1cd2fa430d3a  # Architecture\n\nThe MediaStore system  consists of several\ncomponents.
  S002 2fd8dc202c35  ## Media Cache (v2)\n\n* The cache stores audio files.
  S004 b78ee11673ad  - It is used by the MediaManagement component\n\nThe cache stores audio files.
```

* Splitting is hard-coded: `TextProcessorLocal.java:15, 32-43` sets only `annotators=tokenize,ssplit,pos,parse,depparse,lemma`, the
  dependency annotations and `coref.algorithm`. There is no `ssplit.*` option.
* Injection point: `CoreNLPProvider.java:29, 44` skips processing when `PreprocessingData` is already in the repository.
  `corenlp.TextImpl(CoreDocument)` and `PreprocessingData(Text)` are public. `run presplit` (`Spike.presplit`) uses ArDoCo's properties
  plus `ssplit.eolonly=true`:

```
SENTENCES wrapped.sentences.txt: count=11, rawLines=12 (trailing newline), numbering 0-based contiguous=true, verbatim=11, distinct hashes=9, collisions=[e3428112f622=[2, 3, 5]]
mediastore presplit: {ModelEntityAbsentFromText=6, TextEntityAbsentFromModel=36}; IDENTICAL-INCONSISTENCY-OUTPUT; IDENTICAL-TLR
```

  Caveat: for texts where CoreNLP would split differently, results differ from the standalone runner on the raw file. T3.3 must feed
  the same rendering to both sides. Benchmark texts are already one sentence per line, so nothing changes for them. Artemis maps lines
  the same way as long as the rendering has no blank lines. Its appended `.` changes the text but not the line index.
* Model ids: `PcmExtractor.java:43, 63` uses `getId()` of the parsed `id` attribute. UML uses `UmlElement.java:16`, which is filled from `@XMLValue(name = "xmi:id")` (`PackagedElement.java:14`). The
  spike checks every endpoint id against the model file (`MODEL … ids not found …: []` for PCM and UML). Example:
  `<components__Repository xsi:type="repository:BasicComponent" id="_S-lawHDqEeSqnN80MQ2uGw" entityName="AudioWatermarking"` in
  `ms.repository`. The UML run reports the same ids. Interfaces have no type (`?` above).

## 5. External trace links (V-27)

* What the detectors read: `ConnectionState.getInstanceLinks()` → `TraceLink<RecommendedInstance, ModelEntity>`
  (`core/.../connectiongenerator/ConnectionState.java:29`). The sentence links are derived from them (`getTraceLinks()`, :36-48).
  The users are `ModelEntityAbsentFromTextInconsistencyInformant.java:87` (linked model entities) and
  `TextEntityAbsentFromModelInconsistencyInformant.java:56-60` (linked RIs plus a shared-word filter).
* `ConnectionGenerator` cannot simply be dropped, because it also adds RecommendedInstances (`ReferenceInformant.java:52`,
  `NameTypeConnectionInformant.java:194`), which the text detector uses as candidates.
* Workaround without an upstream change (`Spike.ExternalLinks`): after `ConnectionGenerator`, build `ConnectionStatesImpl.build(...)`
  (public, `ConnectionStatesImpl.java:19`) and add `addToLinks(ri, entity, claimant, p)` for every RI with a word in a linked sentence
  that passes the mapping rule. Then `repo.addData(ConnectionStates.ID, fresh)` replaces the entry (`DataRepository.java:57-61`). The
  spike's mapping rules:

| Link source (sentence, model id) | RI mapping | RI links | `ModelEntityAbsentFromText` | `TextEntityAbsentFromModel` |
|---|---|---|---|---|
| ArDoCo internal (standard runner) | – | 15 | 6 | 36 |
| ArDoCo's own 29 sentence links fed back | `similar` (RI name similar to element) | 11 | 6 | 38: +2 "download" S9/S37, because the internal link RI "download" → `IDownload` is not "similar" |
| same | `sentence` (every RI in the sentence) | 171 | 6 | 0: the shared-word filter removes all candidates |
| same | `internal+similar` (ArDoCo instance link exists, or similar) | 15 | 6 | **36, identical to internal** |
| Gold standard, 31 links | `similar` | 9 | 7: +DB, FileStorage, Reencoding (text says "Database", "DataStorage", "ReEncoder") | 38 |
| same | `sentence` | 151 | **4 = gold UME** | 0 |
| same | `internal+similar` | 9 | 7 | 38 |

* Minimal upstream change: (1) an optional `PipelineStepData` "external SAD–SAM links" (0-based sentence, model id, confidence) with an
  `InconsistencyDetection.setUp(...)` overload that puts it into the repository. (2) `ModelEntityAbsentFromTextInconsistencyInformant`
  takes its linked model ids from that data when present, which gives exact semantics. (3)
  `TextEntityAbsentFromModelInconsistencyInformant` treats an RI as linked when one of its sentences has an external link to an element
  the RI matches (ArDoCo's own instance-link candidates or name similarity, as `internal+similar`). Linking every RI in a linked sentence
  is not an option, because it removes all text findings (table above). The RI↔element pairing stays a heuristic, since sentence-level
  links do not say which mention they cover. That is one new data class and a few dozen changed lines in two informants (estimate). A
  smaller alternative is a `protected` `definePipeline` hook, but it leaves the heuristic in SANDMAN.
* Exact model findings are probably reachable without an upstream change too: run `InconsistencyChecker` twice, once after a `sentence`
  replacement (keep the model findings) and once after an `internal+similar` replacement (keep the text findings). **Not tried.**
* Artemis: `ArtemisConnectionState` has public `addTraceLinks` / `addUnlinkedNamedEntities` (`ArtemisConnectionState.java:17, 19`), so
  the same kind of replacement step would work there, keyed by line numbers. **Not verified** (needs an LLM run).

## 6. Dependencies

`mvn dependency:tree -Dscope=runtime` in `inconsistency-detection/pipeline-id` (excerpt):

```
+- io.github.ardoco.tlr:artemis:jar:2.1.0-SNAPSHOT:compile
|  \- io.github.ardoco:named-architecture-entity-recognition:jar:2.1.0-SNAPSHOT:compile
+- io.github.ardoco.tlr:model-provider:jar:2.1.0-SNAPSHOT:compile
|  +- com.fasterxml.jackson.core:jackson-databind:jar:2.22.2:compile
|  +- dev.langchain4j:langchain4j:jar:1.18.1:compile
|  +- dev.langchain4j:langchain4j-ollama:jar:1.18.1:compile
|  +- dev.langchain4j:langchain4j-open-ai:jar:1.18.1:compile
|  +- io.github.ardoco:magika:jar:0.6.1:compile
|  |  \- com.microsoft.onnxruntime:onnxruntime:jar:1.20.0:compile
|  +- org.eclipse.jdt:org.eclipse.jdt.core:jar:3.46.0:compile   (+ org.eclipse.platform/equinox/osgi, jna 5.18.1)
+- io.github.ardoco.tlr:text-preprocessing:jar:2.1.0-SNAPSHOT:compile
|  +- edu.stanford.nlp:stanford-corenlp:jar:4.5.10:compile
|  |  +- jakarta.servlet:jakarta.servlet-api:jar:4.0.4:compile
|  |  +- com.google.protobuf:protobuf-java:jar:3.25.5:compile
|  |  +- javax.xml.bind:jaxb-api:jar:2.4.0-b180830.0359:compile
|  +- edu.stanford.nlp:stanford-corenlp:jar:models:4.5.10:compile
|  \- org.slf4j:slf4j-api:jar:2.0.18:compile
```

Versions pinned by `io.quarkus.platform:quarkus-bom:3.33.4`, the sandman BOM: jackson-databind 2.21.7, protobuf-java 4.33.2,
jakarta.servlet-api 6.0.0, jna 5.8.0, httpclient5 5.5.2, slf4j-api 2.0.17, javaparser-core 3.28.0, commons-lang3 3.20.0 (same).
LangChain4j and Netty are not in that BOM. a2a-java's own transitive versions were not checked.

## Not verified

* The Artemis variant at runtime (no LLM run), including its sentence/line mapping and an external-link replacement step.
* Behaviour on the Quarkus classpath (BOM downgrades, protobuf 4 with CoreNLP, JNA with the Eclipse platform, Jandex/OSGi jars).
* Concurrent runs in one JVM (singletons `ConfigManager.INSTANCE`, `SimilarityUtils.getInstance()`), and memory for larger projects.
* The external-link heuristics on projects other than MediaStore.
* Builds on Linux / in `maven:3.9-eclipse-temurin-21` (V-26). Only macOS with local JDK 21 and 26 was tested, so Docker was not needed.

## Proposed follow-ups (not applied)

* ADR-0015: B1 → two pinned commits plus the bootstrap order. S2 → S3 "adapter-controlled one-sentence-per-line rendering, mapping by
  index, hash as assertion". Record the V-27 result (workaround plus an upstream extension for exact semantics). V-29: an upstream change
  to accept a `ChatModel`.
* Plan: T1.1 drops "sentenceIndex compatible with ArDoCo's splitting". T3.1 owns the rendering and the `PreprocessingData` injection.
  RK-3 likelihood M → L. RK-18 stays M with the mitigation "workaround now, upstream extension". Add a risk for the unpublished NAER
  snapshot / cross-repo cycle.
