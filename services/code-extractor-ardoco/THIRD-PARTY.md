# Third-party components of the code-extractor-ardoco image (T2.6)

The service brings ArDoCo's code extractors and their libraries into its image.
These libraries run only in this service. The enforcer rules and the ArchUnit test of `tlr-agent` keep them out of `approach-ardoco` and `tlr-agent`.
All licenses allow redistribution in a Docker image of an AGPL-3.0 project (NFR-13).
Re-check on every ArDoCo upgrade (`ardoco.version` in the root `pom.xml`).

| Component | Version | License | Note |
|---|---|---|---|
| ArDoCo (`io.github.ardoco.tlr:model-provider`, `io.github.ardoco.core:common`) | 2.1.0 | MIT | Maven Central, unmodified. LangChain4j and its transitive libraries are excluded. |
| Eclipse JDT core, ECJ and the Eclipse platform jars it needs (`org.eclipse.jdt:*`, `org.eclipse.platform:*`) | 3.46.0 and later platform versions | EPL-2.0 | unmodified, parse Java files |
| Magika for Java (`io.github.ardoco:magika`), with the Magika model of Google | 0.6.1 | Apache-2.0 | unmodified, file type prediction, the model is inside the jar |
| ONNX Runtime (`com.microsoft.onnxruntime:onnxruntime`) | 1.20.0 | MIT | runs the Magika model; its native libraries are unpacked to `/opt/onnxruntime` at build time |
| ANTLR 4 runtime (`org.antlr:antlr4-runtime`) | 4.13.2 | BSD-3-Clause | transitive of `model-provider`, not used on the Java and shell path |
| JavaParser (`com.github.javaparser:javaparser-core`) | 3.28.0 | LGPL-3.0-or-later or Apache-2.0 (dual) | transitive of `model-provider`, unmodified |
| JGit (`org.eclipse.jgit`), Eclipse Collections, JNA | see `mvn dependency:tree` | EDL-1.0 (BSD-3-Clause), EPL-1.0 or EDL-1.0, LGPL-2.1-or-later or Apache-2.0 (dual) | transitive of `model-provider` and JDT |
| Apache Commons (io, lang3, text, codec), Jackson 2, Apache OpenNLP | see `mvn dependency:tree` | Apache-2.0 | transitive of `model-provider` |

Base images (pinned by digest in the `Dockerfile`): `maven:3.9-eclipse-temurin-21` (build stage only) and `eclipse-temurin:21-jre`, both Eclipse Temurin (GPL-2.0 with Classpath Exception).

Benchmark code of the opt-in test `CodeModelEquivalenceTest` (profile `benchmark`) is downloaded at test time and never stored in the repository or the image: TeaStore (Apache-2.0), MediaStore3 (no license stated, download only) and TEAMMATES (GPL-2.0, memory check only).
