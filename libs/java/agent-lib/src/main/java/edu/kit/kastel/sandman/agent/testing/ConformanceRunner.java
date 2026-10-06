package edu.kit.kastel.sandman.agent.testing;

import edu.kit.kastel.sandman.agent.Contracts;
import edu.kit.kastel.sandman.agent.Ids;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.Objects;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

/** Runs the id, key, fingerprint and envelope cases of contracts/conformance/vectors.json. */
public final class ConformanceRunner {

    private ConformanceRunner() {}

    /** Failure descriptions; empty if all cases pass. */
    public static List<String> run(Path vectorsPath, Contracts contracts) {
        JsonNode v = JsonMapper.shared().readTree(vectorsPath);
        List<String> failures = new ArrayList<>();
        for (JsonNode c : v.get("stableId").get("cases")) {
            String got = Ids.stableId(text(c, "prefix"), strings(c.get("parts")).toArray(String[]::new));
            check(failures, "stableId " + c.get("parts"), got, text(c, "expected"));
        }
        for (JsonNode c : v.get("sectionKey").get("cases")) {
            List<Integer> occurrences = new ArrayList<>();
            c.get("occurrences").forEach(n -> occurrences.add(n.asInt()));
            String got = Ids.sectionLocalKey(strings(c.get("headings")), occurrences);
            check(failures, "sectionKey " + c.get("headings"), got, text(c, "expected"));
        }
        for (JsonNode c : v.get("sentenceKey").get("cases")) {
            String got = Ids.sentenceLocalKey(
                    text(c, "sectionLocalKey"),
                    text(c, "text"),
                    c.get("occurrence").asInt());
            check(failures, "sentenceKey " + c.get("text"), got, text(c, "expected"));
        }
        for (JsonNode c : v.get("fingerprint").get("cases")) {
            JsonNode i = c.get("input");
            String fp = Ids.fingerprint(text(i, "type"), text(i, "detector"), strings(i.get("involved")));
            check(failures, "fingerprint " + text(i, "type"), fp, text(c, "expected"));
            check(
                    failures,
                    "inc id " + text(i, "type"),
                    Ids.stableId("inc", text(c, "projectId"), fp),
                    text(c, "expectedInconsistencyId"));
        }
        for (JsonNode c : v.get("eventEnvelope").get("cases")) {
            boolean got = contracts.isValid("events.schema.json", c.get("event"));
            check(
                    failures,
                    "eventEnvelope " + c.get("event").get("id"),
                    got,
                    c.get("valid").asBoolean());
        }
        return failures;
    }

    private static void check(List<String> failures, String label, Object got, Object expected) {
        if (!Objects.equals(got, expected)) {
            failures.add(label + ": got " + got + ", expected " + expected);
        }
    }

    private static String text(JsonNode node, String field) {
        return node.get(field).asString();
    }

    private static List<String> strings(JsonNode array) {
        List<String> out = new ArrayList<>();
        array.forEach(n -> out.add(n.asString()));
        return out;
    }
}
