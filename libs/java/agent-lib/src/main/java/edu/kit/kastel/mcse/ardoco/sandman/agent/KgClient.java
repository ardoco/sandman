package edu.kit.kastel.mcse.ardoco.sandman.agent;

import java.util.List;
import java.util.Map;
import org.neo4j.driver.AuthTokens;
import org.neo4j.driver.Driver;
import org.neo4j.driver.GraphDatabase;
import org.neo4j.driver.Record;
import org.neo4j.driver.Session;
import org.neo4j.driver.TransactionCallback;

/** Knowledge-graph access over Bolt; read() runs in a read transaction (Memgraph rejects writes there). */
public final class KgClient implements AutoCloseable {

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
            return session.executeRead(tx -> tx.run(cypher, params).list(Record::asMap));
        }
    }

    /** Runs a write query; transient errors are retried by the driver. */
    public List<Map<String, Object>> write(String cypher, Map<String, Object> params) {
        return writeTx(tx -> tx.run(cypher, params).list(Record::asMap));
    }

    /** Runs several statements in one write transaction. */
    public <T> T writeTx(TransactionCallback<T> work) {
        try (Session session = driver.session()) {
            return session.executeWrite(work);
        }
    }

    @Override
    public void close() {
        driver.close();
    }
}
