# Third-party components of the tlr-agent image (T2.1)

The approach `ardoco` (module `approach-ardoco`) brings these runtime components into the image.
All licenses allow redistribution in a Docker image of an AGPL-3.0 project (NFR-13).
Re-check on every ArDoCo upgrade (`ardoco.version` in the root `pom.xml`).

| Component | Version | License | Note |
|---|---|---|---|
| ArDoCo (`io.github.ardoco.tlr:*`, `io.github.ardoco.core:*`) | 2.1.0 | MIT | Maven Central, unmodified |
| Stanford CoreNLP code jar (`edu.stanford.nlp:stanford-corenlp`, no models jar) | 4.5.10 | GPL-3.0-or-later | unmodified; ArDoCo classes link against it, but nothing annotates in this process (T2.5); the models jar is not in the image |
| LangChain4j (`dev.langchain4j:*`), transitive of ArDoCo `model-provider` | 1.18.1 | Apache-2.0 | no LLM call on the `ardoco` path (R-030) |
| ONNX Runtime (`com.microsoft.onnxruntime:onnxruntime`), transitive of `magika` | 1.20.0 | MIT | not used on the `ardoco` path |
| Eclipse JDT core and platform jars, transitive of `model-provider` | 3.46.0 | EPL-2.0 | not used on the `ardoco` path |

## Containers next to the image (T2.5)

| Component | Version | License | Note |
|---|---|---|---|
| Provider service image `ghcr.io/ardoco/stanfordcorenlp-provider-service` (compose profile `corenlp-local`) | 1.0.0, pinned by digest | MIT (service); CoreNLP and its models inside: GPL-3.0-or-later | unmodified, separate container; it is the only place where the CoreNLP models run |
| Caddy (`caddy:2.11.7-alpine`) as `corenlp-gateway` | 2.11.7 | Apache-2.0 | unmodified; same pinned image as `edge-proxy` |
