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

/** Knowledge-graph access over Bolt; read() runs in a read transaction (Memgraph rejects writes there). */
public final class KgClient implements AutoCloseable {

    /** Memgraph aborted the query at its memory limit; not retried (Memgraph reports it as transient). */
    public static final class MemoryLimitException extends RuntimeException {
        public MemoryLimitException(String message, Throwable cause) {
            super(message, cause);
        }
    }

    private final Driver driver;

    public KgClient(String uri, String user, String password) {
        this(GraphDatabase.driver(
                uri, user != null ? AuthTokens.basic(user, password != null ? password : "") : AuthTokens.none()));
    }

    public KgClient(Driver driver) {
        this.driver = driver;
    }

    /** Runs a read query; rows as maps. */
    public List<Map<String, Object>> read(String cypher, Map<String, Object> params) {
        try (Session session = driver.session()) {
            return session.executeRead(
                    noRetryOnMemoryLimit(tx -> tx.run(cypher, params).list(Record::asMap)));
        }
    }

    /** Runs a write query; transient errors are retried by the driver. */
    public List<Map<String, Object>> write(String cypher, Map<String, Object> params) {
        return writeTx(tx -> tx.run(cypher, params).list(Record::asMap));
    }

    /** Runs several statements in one write transaction. */
    public <T> T writeTx(TransactionCallback<T> work) {
        try (Session session = driver.session()) {
            return session.executeWrite(noRetryOnMemoryLimit(work));
        }
    }

    /** Auto-commit query; Memgraph requires it for schema statements and SHOW STORAGE INFO (spike T0.4). */
    public List<Map<String, Object>> runAutoCommit(String cypher, Map<String, Object> params) {
        try (Session session = driver.session()) {
            return session.run(cypher, params).list(Record::asMap);
        }
    }

    /** Runs contracts/kg/init.cypher statement by statement (idempotent). */
    public void initSchema(Path cypherFile) {
        try {
            String code = Files.readAllLines(cypherFile, StandardCharsets.UTF_8).stream()
                    .filter(line -> !line.strip().startsWith("//"))
                    .collect(Collectors.joining("\n"));
            for (String statement : code.split(";")) {
                if (!statement.isBlank()) {
                    runAutoCommit(statement.strip(), Map.of());
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
                String message = e.getMessage() == null ? "" : e.getMessage();
                if (message.toLowerCase(java.util.Locale.ROOT).contains("memory limit exceeded")) {
                    throw new MemoryLimitException(message, e);
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
