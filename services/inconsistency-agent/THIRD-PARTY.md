# Third-party components of the inconsistency-agent image (T3.1)

The detectors `ardoco-id` and `artemis-id` (module `detector-ardoco`) bring these runtime components into the image.
All licenses allow redistribution in a Docker image of an AGPL-3.0 project (NFR-13).
Re-check on every ArDoCo upgrade (`ARDOCO_COMMIT` in `scripts/ardoco-id-bootstrap.sh`, `corenlp.version` in the root `pom.xml`).

| Component | Version | License | Note |
|---|---|---|---|
| ArDoCo (`io.github.ardoco.id:*`, `io.github.ardoco.core:*`) | `main` merge commit `21ef6821e1b8fe378ee58ae075c1c05a14fad436` (PR #186, `2.2.0-SNAPSHOT`) | MIT | built from source in the Docker build stage; no patch |
| Stanford CoreNLP code jar (`edu.stanford.nlp:stanford-corenlp`, no models jar) | 4.5.10 | GPL-3.0-or-later | unmodified; ArDoCo classes link against it, but nothing annotates in this process (T2.5); the models jar is not in the image |
| LangChain4j (`dev.langchain4j:*`), transitive of ArDoCo `model-provider` | 1.18.1 | Apache-2.0 | no LLM call on the `ardoco-id` path; `artemis-id` calls the gateway only, through `LlmClient` (R-030) |
| Named architecture entity recognition (`io.github.ardoco:named-architecture-entity-recognition`), used by `artemis-id` | 2.1.0 | MIT | prompts and answer parsing; the chat model is the gateway adapter |
| ONNX Runtime and Magika | not in the image | MIT | excluded in the module `pom.xml`; the enforcer bans them |
| Eclipse JDT core and platform jars | not in the image | EPL-2.0 | excluded in the module `pom.xml`; the enforcer bans them |

The CoreNLP models run in the separate container `corenlp-gateway` upstream (see `services/tlr-agent/THIRD-PARTY.md`).
