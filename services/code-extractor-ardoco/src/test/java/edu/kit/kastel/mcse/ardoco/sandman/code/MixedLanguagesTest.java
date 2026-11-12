package edu.kit.kastel.mcse.ardoco.sandman.code;

import static org.junit.jupiter.api.Assertions.assertEquals;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.Set;
import java.util.TreeSet;
import java.util.stream.Stream;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

class MixedLanguagesTest {

    @TempDir
    Path tmp;

    private Path store() throws Exception {
        Path src = Path.of(getClass().getResource("/fixtures/mixed").toURI()).toRealPath();
        Path dst = tmp.resolve("mixed");
        try (Stream<Path> s = Files.walk(src)) {
            for (Path p : s.toList()) {
                Path t = dst.resolve(src.relativize(p).toString());
                if (Files.isDirectory(p)) {
                    Files.createDirectories(t);
                } else {
                    Files.copy(p, t);
                }
            }
        }
        return dst.toRealPath();
    }

    private static String path(JsonNode f) {
        List<String> parts = new ArrayList<>();
        f.get("pathElements").forEach(e -> parts.add(e.asString()));
        String ext = f.get("extension").asString();
        parts.add(f.get("name").asString() + (ext.isEmpty() ? "" : "." + ext));
        return String.join("/", parts);
    }

    private static ObjectNode codeModelInput(Path store) {
        ObjectNode request = JsonMapper.shared().createObjectNode();
        request.put("projectId", "mixed");
        var current = request.putArray("currentArtifacts");
        Fixtures.files(store)
                .forEach(p -> current.addObject()
                        .put("path", p)
                        .put("storagePath", store.resolve(p).toString()));
        return request;
    }

    @Test
    void twoRunsGiveTheSameExtractAndAcm() throws Exception {
        Path store = store();
        CodeService service = Fixtures.service(store);
        JsonNode extract1 = service.extract(Fixtures.extractInput(store, null));
        JsonNode extract2 = service.extract(Fixtures.extractInput(store, null));
        assertEquals(extract1, extract2);
        JsonNode acm1 = service.codeModel(codeModelInput(store));
        JsonNode acm2 = service.codeModel(codeModelInput(store));
        assertEquals(acm1, acm2);
        Fixtures.CONTRACTS.validate("skills.schema.json#/$defs/PluginCodeModelOutput", acm1);
        List<String> paths = new ArrayList<>();
        acm1.get("acm").get("codeFiles").forEach(f -> paths.add(path(f)));
        assertEquals(
                List.of(
                        "Dockerfile",
                        "cpp/engine.cpp",
                        "cpp/engine.h",
                        "pom.xml",
                        "py/app/service.py",
                        "py/my app/odd name.py",
                        "scripts/run.sh",
                        "src/main/java/com/acme/Greeter.java"),
                paths.stream().distinct().toList());
        Set<String> languages = new TreeSet<>();
        extract1.get("elements").forEach(e -> {
            if ("file".equals(e.get("kind").asString()) && e.has("properties")) {
                languages.add(e.get("properties").get("language").asString());
            }
        });
        assertEquals(Set.of("JAVA", "PYTHON", "SHELL"), languages);
        Set<String> odd = new TreeSet<>();
        extract1.get("elements").forEach(e -> {
            if (e.get("path").asString().startsWith("py/my app/")) {
                odd.add(e.get("path").asString() + "|" + e.get("localKey").asString());
            }
        });
        assertEquals(
                Set.of(
                        "py/my app/odd name.py|py/my app/odd name.py",
                        "py/my app/odd name.py|py/my app/odd name.py#odd"),
                odd);
    }
}
