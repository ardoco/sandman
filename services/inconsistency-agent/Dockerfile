FROM maven:3.9-eclipse-temurin-21 AS build
WORKDIR /src
COPY pom.xml .
COPY libs/java libs/java
COPY services/tlr-agent services/tlr-agent
COPY services/inconsistency-agent services/inconsistency-agent
COPY contracts contracts
RUN --mount=type=cache,target=/root/.m2 mvn -B -q -pl services/inconsistency-agent -am package -DskipTests

FROM eclipse-temurin:21-jre
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/* \
 && useradd --uid 10001 sandman && install -d -o sandman /artifacts
COPY --from=build /src/services/inconsistency-agent/target/quarkus-app /app
COPY contracts /contracts
ENV SANDMAN_CONTRACTS_DIR=/contracts SANDMAN_AGENT_CARD=/contracts/agent-cards/inconsistency-agent.json
USER sandman
EXPOSE 8080
CMD ["java", "-jar", "/app/quarkus-run.jar"]
