package mini;

import mini.MediaCache;

// REQ-1
public class MediaCacheTest {
    public static void main(String[] args) {
        MediaCache cache = new MediaCache();
        cache.put("song.mp3", new byte[] {1});
        if (cache.get("song.mp3") == null) {
            throw new AssertionError("cached media file missing");
        }
    }
}
