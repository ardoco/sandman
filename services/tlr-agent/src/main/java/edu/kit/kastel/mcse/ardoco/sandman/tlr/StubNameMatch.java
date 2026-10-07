package edu.kit.kastel.mcse.ardoco.sandman.tlr;

import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import java.util.TreeSet;
import java.util.regex.Pattern;
import tools.jackson.databind.JsonNode;

/** Links source and target elements whose names share a token (camelCase and punctuation split, lowercase). */
public final class StubNameMatch implements TraceLinkApproach {

    private static final Pattern SPLIT =
            Pattern.compile("(?<=[\\p{Ll}\\p{N}])(?=\\p{Lu})|(?<=\\p{Lu})(?=\\p{Lu}\\p{Ll})|[^\\p{L}\\p{N}]+");

    @Override
    public String id() {
        return "stub-name-match";
    }

    @Override
    public String version() {
        return "1";
    }

    @Override
    public Set<KindPair> supports() {
        return KindPair.all();
    }

    @Override
    public List<CandidateLink> recover(TlrInput in, TlrContext ctx) {
        JsonNode params = ctx.params();
        int minLength = params.path("minTokenLength").asInt(4);
        double confidence = params.path("confidence").asDouble(0.5);
        List<CandidateLink> links = new ArrayList<>();
        for (var source : in.sources()) {
            Set<String> sourceTokens = tokens(source.name(), minLength);
            for (var target : in.targets()) {
                if (source.id().equals(target.id())) {
                    continue;
                }
                Set<String> shared = new TreeSet<>(tokens(target.name(), minLength));
                shared.retainAll(sourceTokens);
                if (!shared.isEmpty()) {
                    links.add(new CandidateLink(
                            source.id(),
                            target.id(),
                            "relates",
                            confidence,
                            "shared tokens: " + String.join(", ", shared)));
                }
            }
        }
        return links;
    }

    static Set<String> tokens(String name, int minLength) {
        Set<String> tokens = new TreeSet<>();
        if (name != null) {
            for (String t : SPLIT.split(name)) {
                if (t.length() >= minLength) {
                    tokens.add(t.toLowerCase(Locale.ROOT));
                }
            }
        }
        return tokens;
    }
}
