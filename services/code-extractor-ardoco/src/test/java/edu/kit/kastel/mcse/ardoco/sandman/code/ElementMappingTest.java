package edu.kit.kastel.mcse.ardoco.sandman.code;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import tools.jackson.databind.JsonNode;

class ElementMappingTest {

    static Path store;
    static JsonNode output;
    static Map<String, JsonNode> byKey = new LinkedHashMap<>();

    @BeforeAll
    static void extract() throws Exception {
        store = Fixtures.mappingFixture();
        output = Fixtures.validateExtract(Fixtures.service(store).extract(Fixtures.extractInput(store, null)));
        for (JsonNode e : output.get("elements")) {
            byKey.put(e.get("path").asString() + "|" + e.get("localKey").asString(), e);
        }
    }

    static JsonNode el(String path, String localKey) {
        JsonNode e = byKey.get(path + "|" + localKey);
        assertNotNull(e, "element " + path + "|" + localKey + " in " + byKey.keySet());
        return e;
    }

    @Test
    void fileElementsCarryPackageAndLanguage() {
        JsonNode file = el("com/acme/Outer.java", "com/acme/Outer.java");
        assertEquals("file", file.get("kind").asString());
        assertEquals("CodeUnit", file.get("label").asString());
        assertEquals("Outer.java", file.get("name").asString());
        assertEquals("com.acme.Outer.java", file.get("qualifiedName").asString());
        assertEquals("JAVA", file.get("properties").get("language").asString());
        assertFalse(file.has("startLine"));
        assertFalse(file.has("parentLocalKey"));
    }

    @Test
    void fileWithoutPackageUsesTheFileNameAsQualifiedName() {
        JsonNode file = el("NoPackage.java", "NoPackage.java");
        assertEquals("NoPackage.java", file.get("qualifiedName").asString());
        JsonNode type = el("NoPackage.java", "NoPackage");
        assertEquals("NoPackage", type.get("qualifiedName").asString());
        assertEquals("NoPackage.java", type.get("parentLocalKey").asString());
        assertEquals(
                "NoPackage#add",
                el("NoPackage.java", "NoPackage#add").get("localKey").asString());
    }

    @Test
    void typesAreKeyedByQualifiedNameWithNestingAndKind() {
        JsonNode outer = el("com/acme/Outer.java", "com.acme.Outer");
        assertEquals("type", outer.get("kind").asString());
        assertEquals("Outer", outer.get("name").asString());
        assertEquals("com.acme.Outer", outer.get("qualifiedName").asString());
        assertEquals("com/acme/Outer.java", outer.get("parentLocalKey").asString());
        assertEquals("class", outer.get("properties").get("typeKind").asString());
        assertEquals(7, outer.get("startLine").asInt());
        assertEquals(49, outer.get("endLine").asInt());

        JsonNode inner = el("com/acme/Outer.java", "com.acme.Outer.Inner");
        assertEquals("Inner", inner.get("name").asString());
        assertEquals("com.acme.Outer", inner.get("parentLocalKey").asString());

        JsonNode color = el("com/acme/Outer.java", "com.acme.Outer.Color");
        assertEquals("class", color.get("properties").get("typeKind").asString());
        assertEquals("com.acme.Outer", color.get("parentLocalKey").asString());

        JsonNode shape = el("com/acme/Shape.java", "com.acme.Shape");
        assertEquals("interface", shape.get("properties").get("typeKind").asString());
    }

    @Test
    void overloadsGetTheOrdinalSuffixInSourceOrder() {
        JsonNode first = el("com/acme/Outer.java", "com.acme.Outer#run");
        JsonNode second = el("com/acme/Outer.java", "com.acme.Outer#run~2");
        JsonNode third = el("com/acme/Outer.java", "com.acme.Outer#run~3");
        assertTrue(first.get("startLine").asInt() < second.get("startLine").asInt());
        assertTrue(second.get("startLine").asInt() < third.get("startLine").asInt());
        assertEquals("run", third.get("name").asString());
        assertEquals("com.acme.Outer#run", third.get("qualifiedName").asString());
        assertEquals("method", third.get("kind").asString());
        assertEquals("com.acme.Outer", third.get("parentLocalKey").asString());
    }

    @Test
    void aMethodOfANestedTypeBelongsOnlyToTheNestedType() {
        assertNotNull(byKey.get("com/acme/Outer.java|com.acme.Outer.Inner#go"));
        assertNotNull(byKey.get("com/acme/Outer.java|com.acme.Outer.Color#paint"));
        assertEquals(null, byKey.get("com/acme/Outer.java|com.acme.Outer#go"));
        assertEquals(null, byKey.get("com/acme/Outer.java|com.acme.Outer#paint"));
    }

    @Test
    void everyLocalKeyIsUniquePerArtifactAndParentsExist() {
        Map<String, Integer> counts = new LinkedHashMap<>();
        for (JsonNode e : output.get("elements")) {
            counts.merge(e.get("path").asString() + "|" + e.get("localKey").asString(), 1, Integer::sum);
        }
        counts.forEach((k, n) -> assertEquals(1, n, k));
        for (JsonNode e : output.get("elements")) {
            if (e.has("parentLocalKey")) {
                assertNotNull(
                        byKey.get(e.get("path").asString() + "|"
                                + e.get("parentLocalKey").asString()),
                        "parent of " + e.get("localKey"));
            }
        }
    }

    @Test
    void shellScriptGivesAFileElementAndItsFunctions() {
        JsonNode file = el("scripts/build.sh", "scripts/build.sh");
        assertEquals("SHELL", file.get("properties").get("language").asString());
        assertEquals("scripts/build.sh", file.get("localKey").asString());
    }

    @Test
    void textFileGivesOnlyTheFileElementAndAWarning() {
        JsonNode file = el("notes.txt", "notes.txt");
        assertEquals("file", file.get("kind").asString());
        assertFalse(file.has("properties"));
        List<String> warnings = new ArrayList<>();
        output.get("warnings").forEach(w -> warnings.add(w.asString()));
        assertTrue(warnings.stream().anyMatch(w -> w.startsWith("notes.txt:")), warnings.toString());
        assertEquals(
                1L,
                byKey.keySet().stream().filter(k -> k.startsWith("notes.txt|")).count());
    }

    @Test
    void testArtifactsGetTestCaseLabelAndTestKinds() {
        String path = "src/test/java/com/acme/OuterTest.java";
        JsonNode file = el(path, path);
        assertEquals("TestCase", file.get("label").asString());
        assertEquals("file", file.get("kind").asString());
        JsonNode type = el(path, "com.acme.OuterTest");
        assertEquals("TestCase", type.get("label").asString());
        assertEquals("test-class", type.get("kind").asString());
        JsonNode method = el(path, "com.acme.OuterTest#runAddsNames");
        assertEquals("TestCase", method.get("label").asString());
        assertEquals("test-method", method.get("kind").asString());
    }

    @Test
    void importsGiveFileDependenciesToCurrentFilesOnly() {
        List<String> deps = new ArrayList<>();
        for (JsonNode r : output.get("relations")) {
            assertEquals("DEPENDS_ON", r.get("type").asString());
            deps.add(r.get("source").get("path").asString() + " -> "
                    + r.get("target").get("path").asString());
            assertEquals(
                    r.get("source").get("localKey").asString(),
                    r.get("source").get("path").asString());
        }
        assertTrue(deps.contains("com/acme/Outer.java -> com/acme/sub/Helper.java"), deps.toString());
        assertEquals(
                1,
                deps.stream().filter(d -> d.startsWith("com/acme/Outer.java")).count(),
                deps.toString());
    }

    @Test
    void relationsOnlyForTheRequestedArtifacts(@TempDir Path tmp) throws Exception {
        JsonNode only =
                Fixtures.service(store).extract(Fixtures.extractInput(store, List.of("com/acme/sub/Helper.java")));
        assertEquals(0, only.get("relations").size());
        assertEquals(
                List.of(
                        "com/acme/sub/Helper.java",
                        "com.acme.sub.Helper",
                        "com.acme.sub.Helper#Helper",
                        "com.acme.sub.Helper#help",
                        "com.acme.sub.Helper#count"),
                keys(only));
        Files.createDirectories(tmp);
    }

    static List<String> keys(JsonNode out) {
        List<String> keys = new ArrayList<>();
        out.get("elements").forEach(e -> keys.add(e.get("localKey").asString()));
        return keys;
    }

    @Test
    void sameInputGivesTheSameOutputAndUnchangedKeysSurviveAnotherFileChange(@TempDir Path tmp) throws Exception {
        JsonNode again = Fixtures.service(store).extract(Fixtures.extractInput(store, null));
        assertEquals(output, again);

        Path copy = tmp.toRealPath().resolve("copy");
        for (String path : Fixtures.files(store)) {
            Files.createDirectories(copy.resolve(path).getParent());
            Files.copy(store.resolve(path), copy.resolve(path));
        }
        Path helper = copy.resolve("com/acme/sub/Helper.java");
        Files.writeString(
                helper,
                Files.readString(helper)
                        .replace(
                                "public static String help()",
                                "public static String other() {\n        return \"x\";\n    }\n\n    public static String help()"));
        List<String> outerBefore = keysOf(output, "com/acme/Outer.java");
        JsonNode changed = Fixtures.service(copy).extract(Fixtures.extractInput(copy, null));
        assertEquals(outerBefore, keysOf(changed, "com/acme/Outer.java"));
        assertTrue(keysOf(changed, "com/acme/sub/Helper.java").contains("com.acme.sub.Helper#other"));
    }

    static List<String> keysOf(JsonNode out, String path) {
        List<String> keys = new ArrayList<>();
        out.get("elements").forEach(e -> {
            if (path.equals(e.get("path").asString())) {
                keys.add(e.get("localKey").asString());
            }
        });
        return keys;
    }

    @Test
    void percentEncodedPathsOfTheAcmAreDecoded() {
        assertEquals("dir with space/My File.java", ElementMapping.decode("dir%20with%20space/My%20File.java"));
        assertEquals("üni/Ünï.java", ElementMapping.decode("%C3%BCni/%C3%9Cn%C3%AF.java"));
        assertEquals("a+b/100%.java", ElementMapping.decode("a+b/100%25.java"));
    }

    /**
     * ArDoCo builds Java paths from {@code URI.relativize} (the whole path percent-encoded, {@code %} as {@code %25}),
     * but shell paths from an encoded directory part and the raw file name.
     */
    @Test
    void fileNamesWithPercentSignsKeepTheirPathForJavaAndShell(@TempDir Path tmp) throws Exception {
        Path fixture = Fixtures.mappingFixture();
        Files.createDirectories(tmp.resolve("100% dir/a b"));
        Map<String, String> sources = Map.of(
                "100% dir/a b/Name%20X.java", "NoPackage.java",
                "100% dir/a b/100%.java", "NoPackage.java",
                "100% dir/a b/run%20it.sh", "scripts/build.sh",
                "100% dir/a b/100%.sh", "scripts/build.sh");
        for (var e : sources.entrySet()) {
            Files.copy(fixture.resolve(e.getValue()), tmp.resolve(e.getKey()));
        }
        JsonNode out = Fixtures.validateExtract(Fixtures.service(tmp).extract(Fixtures.extractInput(tmp, null)));
        Map<String, String> languages = new LinkedHashMap<>();
        for (JsonNode e : out.get("elements")) {
            if ("file".equals(e.get("kind").asString())) {
                assertTrue(e.has("properties"), "recognized: " + e.get("path").asString());
                languages.put(
                        e.get("path").asString(),
                        e.get("properties").get("language").asString());
            }
        }
        assertEquals(
                Map.of(
                        "100% dir/a b/Name%20X.java", "JAVA",
                        "100% dir/a b/100%.java", "JAVA",
                        "100% dir/a b/run%20it.sh", "SHELL",
                        "100% dir/a b/100%.sh", "SHELL"),
                languages);
    }
}
