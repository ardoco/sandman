import java.util.Map;
import java.util.TreeMap;

/** A class in the default package, in a file without package declaration. */
public class NoPackage {
    private final Map<String, Integer> counts = new TreeMap<>();

    public void add(String key) {
        counts.merge(key, 1, Integer::sum);
    }

    public int get(String key) {
        return counts.getOrDefault(key, 0);
    }
}
