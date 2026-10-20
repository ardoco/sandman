package mini;

import java.util.HashMap;
import java.util.Map;

/** Keeps recently used media files in memory, newest first. */
public class MediaCache {
    private final Map<String, byte[]> entries = new HashMap<>();

    public void put(String name, byte[] content) {
        entries.put(name, content);
    }

    public byte[] get(String name) {
        return entries.get(name);
    }
}
