package edu.kit.kastel.mcse.ardoco.sandman.agent;

import java.io.IOException;
import java.io.UncheckedIOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;
import org.neo4j.driver.AuthTokens;
import org.neo4j.driver.Driver;
import org.neo4j.driver.GraphDatabase;
import org.neo4j.driver.Record;
import org.neo4j.driver.Session;
import org.neo4j.driver.TransactionCallback;
import org.neo4j.driver.exceptions.Neo4jException;

/** Knowledge-graph access over Bolt; read() runs in a read transaction (Traverse rejects writes there). */
public final class KgClient implements AutoCloseable {

    /** Traverse aborted the query at its memory limit; not retried (Traverse reports it as transient). */
    public static final class MemoryLimitException extends RuntimeException {
        public MemoryLimitException(String message, Throwable cause) {
            super(message, cause);
        }
    }

    private static final String MEMORY_LIMIT_CODE = "Neo.TransientError.General.MemoryPoolOutOfMemoryError";

    private final Driver driver;

    public KgClient(String uri, String user, String password) {
        this(GraphDatabase.driver(
                uri, user != null ? AuthTokens.basic(user, password != null ? password : "") : AuthTokens.none()));
    }

    public KgClient(Driver driver) {
        this.driver = driver;
    }

    /**
     * Runs a read query; rows as maps. Rule: a query whose rows enter an LLM prompt or set the order of LLM calls must
     * end in ORDER BY on a stable key (e.g. e.id); unordered rows break replay determinism.
     */
    public List<Map<String, Object>> read(String cypher, Map<String, Object> params) {
        return Telemetry.inSpan(Telemetry.kgSpan("read", cypher), () -> {
            try (Session session = driver.session()) {
                return session.executeRead(
                        noRetryOnMemoryLimit(tx -> tx.run(cypher, params).list(Record::asMap)));
            }
        });
    }

    /** Runs a write query; transient errors are retried by the driver. */
    public List<Map<String, Object>> write(String cypher, Map<String, Object> params) {
        return Telemetry.inSpan(Telemetry.kgSpan("write", cypher), () -> {
            try (Session session = driver.session()) {
                return session.executeWrite(
                        noRetryOnMemoryLimit(tx -> tx.run(cypher, params).list(Record::asMap)));
            }
        });
    }

    /** Runs several statements in one write transaction. */
    public <T> T writeTx(TransactionCallback<T> work) {
        return Telemetry.inSpan(Telemetry.kgSpan("write_tx", null), () -> {
            try (Session session = driver.session()) {
                return session.executeWrite(noRetryOnMemoryLimit(work));
            }
        });
    }

    /** Auto-commit query; schema statements run outside an explicit transaction. */
    public List<Map<String, Object>> runAutoCommit(String cypher, Map<String, Object> params) {
        return Telemetry.inSpan(Telemetry.kgSpan("autocommit", cypher), () -> {
            try (Session session = driver.session()) {
                return session.run(cypher, params).list(Record::asMap);
            }
        });
    }

    /**
     * Runs contracts/kg/init.cypher statement by statement (idempotent). CREATE INDEX and CREATE VECTOR INDEX have no IF NOT EXISTS in
     * Traverse: an "already exists" error is ignored.
     */
    public void initSchema(Path cypherFile) {
        try {
            String code = Files.readAllLines(cypherFile, StandardCharsets.UTF_8).stream()
                    .filter(line -> !line.strip().startsWith("//"))
                    .collect(Collectors.joining("\n"));
            for (String statement : code.split(";")) {
                if (!statement.isBlank()) {
                    try {
                        runAutoCommit(statement.strip(), Map.of());
                    } catch (Neo4jException e) {
                        if (e.getMessage() == null || !e.getMessage().contains("already exists")) {
                            throw e;
                        }
                    }
                }
            }
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }
    }

    // the driver retries transient errors; rethrowing a non-driver exception stops the retry loop
    private static <T> TransactionCallback<T> noRetryOnMemoryLimit(TransactionCallback<T> work) {
        return tx -> {
            try {
                return work.execute(tx);
            } catch (Neo4jException e) {
                if (MEMORY_LIMIT_CODE.equals(e.code())) {
                    throw new MemoryLimitException(e.getMessage(), e);
                }
                throw e;
            }
        };
    }

    @Override
    public void close() {
        driver.close();
    }
}
