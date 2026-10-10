# Third-party components of the tlr-agent image (T2.1)

The approach `ardoco` (module `approach-ardoco`) brings these runtime components into the image.
All licenses allow redistribution in a Docker image of an AGPL-3.0 project (NFR-13).
Re-check on every ArDoCo upgrade (`ardoco.version` in the root `pom.xml`).

| Component | Version | License | Note |
|---|---|---|---|
| ArDoCo (`io.github.ardoco.tlr:*`, `io.github.ardoco.core:*`) | 2.1.0 | MIT | Maven Central, unmodified |
| Stanford CoreNLP and its models jar (`edu.stanford.nlp:stanford-corenlp`, classifier `models`) | 4.5.10 | GPL-3.0-or-later | unmodified, used locally for tokenization, POS tags, parse and lemmas; GPL-3.0-or-later is compatible with AGPL-3.0 |
| LangChain4j (`dev.langchain4j:*`), transitive of ArDoCo `model-provider` | 1.18.1 | Apache-2.0 | no LLM call on the `ardoco` path (R-030) |
| ONNX Runtime (`com.microsoft.onnxruntime:onnxruntime`), transitive of `magika` | 1.20.0 | MIT | not used on the `ardoco` path |
| Eclipse JDT core and platform jars, transitive of `model-provider` | 3.46.0 | EPL-2.0 | not used on the `ardoco` path |
