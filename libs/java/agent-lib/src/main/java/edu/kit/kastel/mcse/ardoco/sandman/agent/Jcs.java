package edu.kit.kastel.mcse.ardoco.sandman.agent;

import java.io.IOException;
import java.io.UncheckedIOException;
import org.erdtman.jcs.JsonCanonicalizer;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

/** RFC 8785 (JCS) serialization, e.g. for Run.configHash. */
public final class Jcs {

    private Jcs() {}

    public static byte[] canonicalize(JsonNode value) {
        try {
            return new JsonCanonicalizer(JsonMapper.shared().writeValueAsString(value)).getEncodedUTF8();
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }
    }
}
