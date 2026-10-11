package edu.kit.kastel.mcse.ardoco.sandman.code;

import java.util.Map;

/** Per-call caps on the staged files (SANDMAN_CODE_MAX_FILES, SANDMAN_CODE_MAX_BYTES, SANDMAN_CODE_MAX_FILE_BYTES). The helper parses any positive env number. */
record Limits(int maxFiles, long maxBytes, long maxFileBytes) {

    static final int DEFAULT_FILES = 20_000;
    static final long DEFAULT_BYTES = 64L * 1024 * 1024;

    static final long DEFAULT_FILE_BYTES = 4L * 1024 * 1024;

    Limits(int maxFiles, long maxBytes) {
        this(maxFiles, maxBytes, DEFAULT_FILE_BYTES);
    }

    static Limits fromEnv(Map<String, String> env) {
        return new Limits(
                (int) number(env, "SANDMAN_CODE_MAX_FILES", DEFAULT_FILES, Integer.MAX_VALUE),
                number(env, "SANDMAN_CODE_MAX_BYTES", DEFAULT_BYTES, Long.MAX_VALUE),
                number(env, "SANDMAN_CODE_MAX_FILE_BYTES", DEFAULT_FILE_BYTES, Long.MAX_VALUE));
    }

    static long number(Map<String, String> env, String name, long fallback, long max) {
        String value = env.get(name);
        if (value == null || value.isBlank()) {
            return fallback;
        }
        try {
            long parsed = Long.parseLong(value.strip());
            if (parsed < 1 || parsed > max) {
                throw new IllegalArgumentException(name + " must be between 1 and " + max + ": " + value);
            }
            return parsed;
        } catch (NumberFormatException e) {
            throw new IllegalArgumentException(name + " is not a number: " + value, e);
        }
    }
}
