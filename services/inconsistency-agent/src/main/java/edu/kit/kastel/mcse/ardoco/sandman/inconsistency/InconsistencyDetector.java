package edu.kit.kastel.mcse.ardoco.sandman.inconsistency;

import java.util.List;
import java.util.Set;

/** Detector SPI (ServiceLoader). Must read only active links and current elements. */
public interface InconsistencyDetector {

    /** Stable id without version; part of the fingerprint. */
    String id();

    String version();

    Set<String> types();

    List<Finding> detect(DetectionScope scope, DetectorContext ctx);

    /** Findings plus warnings of the detector or its plugin (R-021). */
    record Detection(List<Finding> findings, List<String> warnings) {}

    default Detection detectWithWarnings(DetectionScope scope, DetectorContext ctx) {
        return new Detection(detect(scope, ctx), List.of());
    }

    default boolean usesLlm() {
        return false;
    }

    default String kind() {
        return "in-process";
    }

    default String description() {
        return null;
    }
}
