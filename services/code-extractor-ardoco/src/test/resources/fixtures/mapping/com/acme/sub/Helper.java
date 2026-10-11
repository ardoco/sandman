package com.acme.sub;

import java.time.Instant;

/** Static helper used by Outer. */
public final class Helper {
    private Helper() {}

    public static String help() {
        return "help at " + Instant.EPOCH;
    }

    public static int count(String text) {
        return text == null ? 0 : text.length();
    }
}
