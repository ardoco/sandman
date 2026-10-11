FROM maven:3.9-eclipse-temurin-21@sha256:99e61abcff91a9b1333463bd8451fb18495d6eba9250ac66a338b518f8278320 AS build
WORKDIR /src
COPY pom.xml .
COPY libs/java libs/java
COPY services/tlr-agent/pom.xml services/tlr-agent/pom.xml
COPY services/tlr-agent/approach-spi/pom.xml services/tlr-agent/approach-spi/pom.xml
COPY services/tlr-agent/approach-ardoco/pom.xml services/tlr-agent/approach-ardoco/pom.xml
COPY services/inconsistency-agent/pom.xml services/inconsistency-agent/pom.xml
COPY services/code-extractor-ardoco services/code-extractor-ardoco
COPY contracts contracts
RUN --mount=type=cache,target=/root/.m2 mvn -B -q -pl services/code-extractor-ardoco -am package -DskipTests
# onnxruntime would unpack its native library to java.io.tmpdir at run time; compose mounts /tmp noexec (tmpfs), where
# dlopen fails, so ship it unpacked and point -Donnxruntime.native.path at it.
RUN mkdir /onnx-unpack /opt/onnxruntime && cd /onnx-unpack \
 && jar xf /src/services/code-extractor-ardoco/target/quarkus-app/lib/main/com.microsoft.onnxruntime.onnxruntime-*.jar \
 && case "$(uname -m)" in x86_64) dir=linux-x64 ;; aarch64) dir=linux-aarch64 ;; *) exit 1 ;; esac \
 && cp ai/onnxruntime/native/$dir/*.so /opt/onnxruntime/

# Magika (file typing) and Eclipse JDT (Java parsing) are inside the jar; the image downloads nothing at run time.
FROM eclipse-temurin:21-jre@sha256:cff19e6215689161eb6162c11b86b0c60ddf802164f2eaf48d570f8fb79a36c5
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/* \
 && useradd --uid 10001 sandman && install -d -o sandman /artifacts
COPY --from=build /src/services/code-extractor-ardoco/target/quarkus-app /app
COPY --from=build /opt/onnxruntime /opt/onnxruntime
COPY contracts /contracts
ENV SANDMAN_CONTRACTS_DIR=/contracts SANDMAN_AGENT_CARD=/contracts/agent-cards/code-extractor-ardoco.json
USER sandman
EXPOSE 8000
CMD ["java", "-Donnxruntime.native.path=/opt/onnxruntime", "-jar", "/app/quarkus-run.jar"]
