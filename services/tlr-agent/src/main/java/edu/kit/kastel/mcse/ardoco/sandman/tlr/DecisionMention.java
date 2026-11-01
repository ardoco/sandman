package edu.kit.kastel.mcse.ardoco.sandman.tlr;

import java.util.ArrayList;
import java.util.List;
import java.util.Set;
import java.util.regex.Pattern;

/** Links a Decision to the model and code elements that its name or text mentions by path or whole-word name. */
public final class DecisionMention implements TraceLinkApproach {

    private static final String WORD = "[\\p{L}\\p{N}_]";
    private static final String FILE_KEY = "file";

    @Override
    public String id() {
        return "decision-mention";
    }

    @Override
    public String version() {
        return "1";
    }

    @Override
    public Set<KindPair> supports() {
        return Set.of(new KindPair("architecture-doc", "architecture-model"), new KindPair("architecture-doc", "code"));
    }

    @Override
    public List<CandidateLink> recover(TlrInput in, TlrContext ctx) {
        int minNameLength = ctx.params().path("minNameLength").asInt(4);
        List<CandidateLink> links = new ArrayList<>();
        for (var source : in.sources()) {
            if (!"Decision".equals(source.label())) {
                continue;
            }
            String text = source.name() + "\n" + (source.text() == null ? "" : source.text());
            for (var target : in.targets()) {
                if (!isTarget(target)) {
                    continue;
                }
                if ("CodeUnit".equals(target.label())
                        && "file".equals(target.kind())
                        && !FILE_KEY.equals(target.localKey())
                        && text.contains(target.localKey())) {
                    links.add(link(source, target, 1.0, "mentions path '" + target.localKey() + "'"));
                } else if (target.name() != null
                        && target.name().length() >= minNameLength
                        && wholeWord(text, target.name())) {
                    links.add(link(source, target, 0.8, "mentions '" + target.name() + "'"));
                }
            }
        }
        return links;
    }

    static boolean isTarget(TlrInput.Element e) {
        return switch (String.valueOf(e.label())) {
            case "ModelElement" -> Set.of("component", "interface").contains(e.kind());
            case "CodeUnit" -> Set.of("file", "type").contains(e.kind());
            default -> false;
        };
    }

    static boolean wholeWord(String text, String name) {
        return Pattern.compile("(?<!" + WORD + ")" + Pattern.quote(name) + "(?!" + WORD + ")")
                .matcher(text)
                .find();
    }

    private static CandidateLink link(TlrInput.Element source, TlrInput.Element target, double confidence, String why) {
        return new CandidateLink(source.id(), target.id(), "relates", confidence, why);
    }
}
