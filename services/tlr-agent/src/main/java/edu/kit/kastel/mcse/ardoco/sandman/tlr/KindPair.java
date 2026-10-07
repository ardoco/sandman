package edu.kit.kastel.mcse.ardoco.sandman.tlr;

import java.util.List;
import java.util.Set;
import java.util.stream.Collectors;

/** Source and target artifact kind (common.schema.json ArtifactKind). */
public record KindPair(String source, String target) {

    private static final List<String> KINDS =
            List.of("requirements", "architecture-doc", "architecture-model", "code", "test", "other");

    /** Every kind combination. */
    public static Set<KindPair> all() {
        return KINDS.stream()
                .flatMap(s -> KINDS.stream().map(t -> new KindPair(s, t)))
                .collect(Collectors.toSet());
    }
}
