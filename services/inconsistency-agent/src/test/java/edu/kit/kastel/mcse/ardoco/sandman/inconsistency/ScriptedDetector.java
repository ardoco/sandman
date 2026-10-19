package edu.kit.kastel.mcse.ardoco.sandman.inconsistency;

import java.util.List;
import java.util.Set;

/** Test detector with scripted findings, failure and a hook run during detect(). */
final class ScriptedDetector implements InconsistencyDetector {

    volatile String version = "1";
    volatile List<Finding> findings = List.of();
    volatile RuntimeException error;
    volatile Runnable onDetect = () -> {};
    volatile ScopeMode mode = ScopeMode.PAIRWISE;
    volatile String identity;
    volatile DetectorContext seen;

    @Override
    public ScopeMode scopeMode() {
        return mode;
    }

    @Override
    public Set<String> paramKeys() {
        return Set.of("p");
    }

    @Override
    public String configIdentity(DetectorContext ctx) {
        return identity;
    }

    @Override
    public String id() {
        return "scripted";
    }

    @Override
    public String version() {
        return version;
    }

    @Override
    public Set<String> types() {
        return Set.of("scripted-finding");
    }

    @Override
    public List<Finding> detect(DetectionScope scope, DetectorContext ctx) {
        seen = ctx;
        onDetect.run();
        if (error != null) {
            throw error;
        }
        return findings;
    }

    static Finding finding(String... elementKeys) {
        return new Finding(
                "scripted-finding",
                "info",
                "scripted",
                null,
                null,
                java.util.Arrays.stream(elementKeys)
                        .map(k -> Finding.Involved.element(k, "subject"))
                        .toList(),
                null);
    }
}
