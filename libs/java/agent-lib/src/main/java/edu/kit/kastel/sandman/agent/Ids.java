package edu.kit.kastel.sandman.agent;

import static java.nio.charset.StandardCharsets.UTF_8;

import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.text.Normalizer;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collection;
import java.util.Comparator;
import java.util.HexFormat;
import java.util.List;
import java.util.UUID;
import java.util.regex.Pattern;

/** Deterministic ids, keys and fingerprints (contracts/conformance/vectors.json). */
public final class Ids {

    public static final String SEP = "\u001f";
    private static final Pattern NON_SLUG = Pattern.compile("[^a-z0-9]+");
    private static final Pattern ASCII_WS = Pattern.compile("[ \\t\\n\\r\\f\\x0B]+"); // ASCII only, identical in Python
    private static final Comparator<String> UTF8_ORDER =
            Comparator.comparing(s -> s.getBytes(UTF_8), Arrays::compareUnsigned);

    private Ids() {}

    public static String sha256Hex(byte[] data) {
        try {
            return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(data));
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException(e);
        }
    }

    public static String sha256Hex(String text) {
        return sha256Hex(text.getBytes(UTF_8));
    }

    /** prefix + '_' + first 32 hex chars of sha256(parts joined by U+001F). */
    public static String stableId(String prefix, String... parts) {
        return prefix + "_" + sha256Hex(String.join(SEP, parts)).substring(0, 32);
    }

    private static String slug(String heading) {
        StringBuilder text = new StringBuilder();
        Normalizer.normalize(heading, Normalizer.Form.NFC)
                .codePoints()
                .forEach(c -> text.appendCodePoint(c >= 'A' && c <= 'Z' ? c + ('a' - 'A') : c));
        String slug = NON_SLUG.matcher(text).replaceAll("-").replaceAll("^-+|-+$", "");
        return slug.isEmpty() ? "section" : slug;
    }

    /** 'sec:' + heading slugs from the top; the n-th sibling with the same slug gets '~n'. */
    public static String sectionLocalKey(List<String> headings, List<Integer> occurrences) {
        if (headings.size() != occurrences.size()) {
            throw new IllegalArgumentException("headings and occurrences differ in length");
        }
        List<String> parts = new ArrayList<>();
        for (int i = 0; i < headings.size(); i++) {
            int n = occurrences.get(i);
            parts.add(slug(headings.get(i)) + (n >= 2 ? "~" + n : ""));
        }
        return "sec:" + String.join("/", parts);
    }

    /** Section key + '/' + 12 hex of sha256(normalized text), '~n' for the n-th identical sentence. */
    public static String sentenceLocalKey(String sectionKey, String text, int occurrence) {
        String normalized = ASCII_WS.matcher(Normalizer.normalize(text, Normalizer.Form.NFC))
                .replaceAll(" ")
                .replaceAll("^ | $", "");
        return sectionKey + "/" + sha256Hex(normalized).substring(0, 12) + (occurrence >= 2 ? "~" + occurrence : "");
    }

    /** sha256 over type, detector id (no version) and involved keys sorted byte-wise by UTF-8. */
    public static String fingerprint(String type, String detectorId, Collection<String> involvedKeys) {
        List<String> parts = new ArrayList<>(List.of(type, detectorId));
        involvedKeys.stream().sorted(UTF8_ORDER).forEach(parts::add);
        return sha256Hex(String.join(SEP, parts));
    }

    public static String newRunId() {
        return "run_" + uuidHex();
    }

    public static String newWorkflowId() {
        return "wf_" + uuidHex();
    }

    private static String uuidHex() {
        return UUID.randomUUID().toString().replace("-", "");
    }
}
