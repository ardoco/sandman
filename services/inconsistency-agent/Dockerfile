FROM maven:3.9-eclipse-temurin-21@sha256:99e61abcff91a9b1333463bd8451fb18495d6eba9250ac66a338b518f8278320 AS build
WORKDIR /src
COPY pom.xml .
COPY libs/java libs/java
COPY services/tlr-agent services/tlr-agent
COPY services/inconsistency-agent services/inconsistency-agent
COPY contracts contracts
RUN --mount=type=cache,target=/root/.m2 mvn -B -q -pl services/inconsistency-agent -am package -DskipTests

FROM eclipse-temurin:21-jre@sha256:cff19e6215689161eb6162c11b86b0c60ddf802164f2eaf48d570f8fb79a36c5
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/* \
 && useradd --uid 10001 sandman && install -d -o sandman /artifacts
COPY --from=build /src/services/inconsistency-agent/target/quarkus-app /app
COPY contracts /contracts
ENV SANDMAN_CONTRACTS_DIR=/contracts SANDMAN_AGENT_CARD=/contracts/agent-cards/inconsistency-agent.json
USER sandman
EXPOSE 8080
CMD ["java", "-jar", "/app/quarkus-run.jar"]
