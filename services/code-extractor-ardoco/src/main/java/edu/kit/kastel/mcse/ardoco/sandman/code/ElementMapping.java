package edu.kit.kastel.mcse.ardoco.sandman.code;

import edu.kit.kastel.mcse.ardoco.core.api.models.CodeModel;
import edu.kit.kastel.mcse.ardoco.core.api.models.code.CodeCompilationUnit;
import edu.kit.kastel.mcse.ardoco.core.api.models.code.CodeItem;
import edu.kit.kastel.mcse.ardoco.core.api.models.code.CodePackage;
import edu.kit.kastel.mcse.ardoco.core.api.models.code.ControlElement;
import edu.kit.kastel.mcse.ardoco.core.api.models.code.Datatype;
import edu.kit.kastel.mcse.ardoco.core.api.models.code.InterfaceUnit;
import edu.kit.kastel.mcse.ardoco.core.api.models.code.ProgrammingLanguage;
import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.IdentityHashMap;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ArrayNode;
import tools.jackson.databind.node.ObjectNode;

/**
 * Maps ArDoCo's code model (ACM) to {@code PluginExtractOutput}. ACM ids are not used: ids depend on extraction
 * order, so keys are built from paths, qualified names and method names (T2.6 mapping table).
 */
final class ElementMapping {

    /** An artifact the caller wants elements for; {@code kind} is {@code code} or {@code test}. */
    record Artifact(String path, String kind) {}

    /** Elements, relations and warnings of one call. */
    record Result(ArrayNode elements, ArrayNode relations, List<String> warnings) {
        ObjectNode toJson() {
            ObjectNode out = JsonMapper.shared().createObjectNode();
            out.set("elements", elements);
            out.set("relations", relations);
            ArrayNode w = out.putArray("warnings");
            warnings.forEach(w::add);
            return out;
        }
    }

    private record Located(String path, String localKey) {}

    /** A type of a compilation unit with its keys. */
    private record TypeInfo(Datatype type, String qualifiedName, String localKey, Located parent) {}

    private final Set<String> currentPaths;
    private final Map<String, CodeCompilationUnit> unitByPath = new LinkedHashMap<>();
    private final Map<CodeCompilationUnit, Map<Datatype, TypeInfo>> typesByUnit = new IdentityHashMap<>();
    private final Map<Datatype, TypeInfo> infoByType = new IdentityHashMap<>();
    private final Map<String, List<Located>> typesByQualifiedName = new HashMap<>();

    private ElementMapping(Set<String> currentPaths) {
        this.currentPaths = currentPaths;
    }

    /** {@code currentPaths}: project paths of all current files (targets of relations). */
    static Result map(CodeModel model, List<Artifact> artifacts, Set<String> currentPaths) {
        ElementMapping mapping = new ElementMapping(currentPaths);
        mapping.index(model);
        return mapping.output(artifacts);
    }

    /** Percent-decodes a path of the ACM (ArDoCo stores {@code URI}-encoded file names). */
    static String decode(String encoded) {
        try {
            return URLDecoder.decode(encoded.replace("+", "%2B"), StandardCharsets.UTF_8);
        } catch (IllegalArgumentException e) {
            return encoded;
        }
    }

    /**
     * ArDoCo 2.1.0 builds Java paths from {@code URI.relativize} (all of it percent-encoded, {@code %} as {@code %25})
     * but shell paths from encoded directories and the raw file name, so a shell file name is not decoded.
     */
    private static String pathOf(CodeCompilationUnit unit) {
        String path = unit.getPath();
        if (unit.getLanguage() == ProgrammingLanguage.JAVA) {
            return decode(path);
        }
        int name = path.lastIndexOf('/') + 1;
        return decode(path.substring(0, name)) + path.substring(name);
    }

    private void index(CodeModel model) {
        List<CodeCompilationUnit> units = new ArrayList<>();
        collect(model.getContent(), units);
        units.sort(Comparator.comparing(ElementMapping::pathOf));
        for (CodeCompilationUnit unit : units) {
            unitByPath.putIfAbsent(pathOf(unit), unit);
        }
        for (CodeCompilationUnit unit : unitByPath.values()) {
            Map<Datatype, TypeInfo> infos = typeInfos(unit);
            typesByUnit.put(unit, infos);
            infos.forEach((type, info) -> {
                infoByType.put(type, info);
                typesByQualifiedName
                        .computeIfAbsent(info.qualifiedName(), k -> new ArrayList<>())
                        .add(new Located(pathOf(unit), info.localKey()));
            });
        }
    }

    private static void collect(List<? extends CodeItem> items, List<CodeCompilationUnit> out) {
        for (CodeItem item : items) {
            if (item instanceof CodeCompilationUnit unit) {
                out.add(unit);
            } else if (item instanceof CodePackage pkg) {
                collect(pkg.getContent(), out);
            }
        }
    }

    private static String packagePrefix(CodeCompilationUnit unit) {
        List<String> names = unit.getParentPackageNames();
        return names.isEmpty() ? "" : String.join(".", names) + ".";
    }

    private static List<Datatype> typesOf(CodeCompilationUnit unit) {
        List<Datatype> types = new ArrayList<>();
        for (CodeItem item : unit.getContent()) {
            if (item instanceof Datatype type) {
                types.add(type);
            }
        }
        types.sort(Comparator.comparingInt(Datatype::getStartLine)
                .thenComparing(Comparator.comparingInt(Datatype::getEndLine).reversed())
                .thenComparing(Datatype::getName));
        return types;
    }

    private Map<Datatype, TypeInfo> typeInfos(CodeCompilationUnit unit) {
        String prefix = packagePrefix(unit);
        String path = pathOf(unit);
        Map<Datatype, String> qualified = new IdentityHashMap<>();
        for (Datatype type : typesOf(unit)) {
            qualified.put(type, prefix + chain(type));
        }
        Map<String, Integer> seen = new HashMap<>();
        Map<Datatype, TypeInfo> infos = new LinkedHashMap<>();
        Map<Datatype, String> keys = new IdentityHashMap<>();
        for (Datatype type : typesOf(unit)) {
            String qn = qualified.get(type);
            int n = seen.merge(qn, 1, Integer::sum);
            // ponytail: positional ~n for duplicate qualified names; ArDoCo 2.1.0 offers nothing to tell them apart
            String key = n >= 2 ? qn + "~" + n : qn;
            keys.put(type, key);
            Datatype parent = type.getParentDatatype();
            Located parentLocator = parent != null && keys.containsKey(parent)
                    ? new Located(path, keys.get(parent))
                    : new Located(path, path);
            infos.put(type, new TypeInfo(type, qn, key, parentLocator));
        }
        return infos;
    }

    private static String chain(Datatype type) {
        List<String> names = new ArrayList<>();
        for (Datatype t = type; t != null; t = t.getParentDatatype()) {
            names.addFirst(t.getName());
        }
        return String.join(".", names);
    }

    private Result output(List<Artifact> artifacts) {
        ArrayNode elements = JsonMapper.shared().createArrayNode();
        ArrayNode relations = JsonMapper.shared().createArrayNode();
        List<String> warnings = new ArrayList<>();
        for (Artifact artifact : artifacts) {
            CodeCompilationUnit unit = unitByPath.get(artifact.path());
            if (unit == null) {
                elements.add(fileElement(artifact, null));
                warnings.add(artifact.path() + ": no Java or shell code recognized, only the file element is returned");
                continue;
            }
            elements.add(fileElement(artifact, unit));
            typeAndMethodElements(artifact, unit, elements);
            relationsOf(artifact, unit, relations);
        }
        return new Result(elements, relations, warnings);
    }

    private ObjectNode fileElement(Artifact artifact, CodeCompilationUnit unit) {
        String name = artifact.path().substring(artifact.path().lastIndexOf('/') + 1);
        ObjectNode e = element(artifact, artifact.path(), "file", name);
        if (unit != null) {
            e.put("qualifiedName", packagePrefix(unit) + name);
            e.putObject("properties").put("language", unit.getLanguage().name());
        }
        return e;
    }

    private static ObjectNode element(Artifact artifact, String localKey, String kind, String name) {
        ObjectNode e = JsonMapper.shared().createObjectNode();
        e.put("path", artifact.path());
        e.put("localKey", localKey);
        e.put("label", "test".equals(artifact.kind()) ? "TestCase" : "CodeUnit");
        e.put("kind", kind);
        e.put("name", name);
        return e;
    }

    private void typeAndMethodElements(Artifact artifact, CodeCompilationUnit unit, ArrayNode out) {
        boolean test = "test".equals(artifact.kind());
        Map<Datatype, TypeInfo> infos = typesByUnit.get(unit);
        Map<Datatype, List<ControlElement>> own = ownMethods(unit, infos);
        for (TypeInfo info : infos.values()) {
            Datatype type = info.type();
            ObjectNode e = element(artifact, info.localKey(), test ? "test-class" : "type", type.getName());
            e.put("qualifiedName", info.qualifiedName());
            lines(e, type.getStartLine(), type.getEndLine());
            e.put("parentLocalKey", info.parent().localKey());
            ObjectNode props = e.putObject("properties");
            props.put("typeKind", type instanceof InterfaceUnit ? "interface" : "class");
            props.put("language", unit.getLanguage().name());
            out.add(e);
            methods(artifact, info.localKey(), info.qualifiedName(), own.get(type), out);
        }
        List<ControlElement> loose = new ArrayList<>();
        for (CodeItem item : unit.getContent()) {
            if (item instanceof ControlElement m) {
                loose.add(m);
            }
        }
        methods(artifact, artifact.path(), artifact.path(), loose, out);
    }

    private static void methods(
            Artifact artifact, String ownerKey, String qualifiedOwner, List<ControlElement> methods, ArrayNode out) {
        if (methods == null) {
            return;
        }
        List<ControlElement> sorted = new ArrayList<>(methods);
        sorted.sort(Comparator.comparingInt(ControlElement::getStartLine)
                .thenComparingInt(ControlElement::getEndLine)
                .thenComparing(ControlElement::getName));
        Map<String, Integer> seen = new HashMap<>();
        for (ControlElement method : sorted) {
            int n = seen.merge(method.getName(), 1, Integer::sum);
            // ponytail: ArDoCo 2.1.0 ControlElement has no parameter types, so the overload suffix is positional (~n);
            // inserting an earlier overload renames the keys of later ones. Use parameter types once ArDoCo exposes
            // them.
            String key = ownerKey + "#" + method.getName() + (n >= 2 ? "~" + n : "");
            ObjectNode e =
                    element(artifact, key, "test".equals(artifact.kind()) ? "test-method" : "method", method.getName());
            e.put("qualifiedName", qualifiedOwner + "#" + method.getName());
            lines(e, method.getStartLine(), method.getEndLine());
            e.put("parentLocalKey", ownerKey);
            out.add(e);
        }
    }

    /** ArDoCo reports -1 for a line it could not determine (an end line at the end of a file without final newline). */
    private static void lines(ObjectNode e, int start, int end) {
        if (start > 0) {
            e.put("startLine", start);
            if (end >= start) {
                e.put("endLine", end);
            }
        }
    }

    /**
     * ArDoCo lists the methods of a nested type again in every enclosing type. A method belongs to the innermost type
     * that lists it: smallest line range, then deepest nesting.
     */
    private static Map<Datatype, List<ControlElement>> ownMethods(
            CodeCompilationUnit unit, Map<Datatype, TypeInfo> infos) {
        record Signature(String name, int start, int end) {}
        Map<Signature, List<Datatype>> listedIn = new HashMap<>();
        for (Datatype type : infos.keySet()) {
            for (CodeItem item : type.getContent()) {
                if (item instanceof ControlElement m) {
                    listedIn.computeIfAbsent(
                                    new Signature(m.getName(), m.getStartLine(), m.getEndLine()),
                                    k -> new ArrayList<>())
                            .add(type);
                }
            }
        }
        Comparator<Datatype> innermost = Comparator.comparingInt((Datatype t) -> t.getEndLine() - t.getStartLine())
                .thenComparing(Comparator.comparingInt(ElementMapping::depth).reversed());
        Map<Datatype, List<ControlElement>> own = new IdentityHashMap<>();
        for (Datatype type : infos.keySet()) {
            List<ControlElement> mine = new ArrayList<>();
            for (CodeItem item : type.getContent()) {
                if (item instanceof ControlElement m) {
                    List<Datatype> candidates =
                            listedIn.get(new Signature(m.getName(), m.getStartLine(), m.getEndLine()));
                    if (candidates.stream().min(innermost).orElseThrow() == type) {
                        mine.add(m);
                    }
                }
            }
            own.put(type, mine);
        }
        return own;
    }

    private static int depth(Datatype type) {
        int depth = 0;
        for (Datatype t = type.getParentDatatype(); t != null; t = t.getParentDatatype()) {
            depth++;
        }
        return depth;
    }

    private void relationsOf(Artifact artifact, CodeCompilationUnit unit, ArrayNode out) {
        Set<String> emitted = new LinkedHashSet<>();
        for (TypeInfo info : typesByUnit.get(unit).values()) {
            Located source = new Located(artifact.path(), info.localKey());
            Set<Datatype> targets = new LinkedHashSet<>(info.type().getExtendedTypes());
            targets.addAll(info.type().getImplementedTypes());
            for (Datatype target : targets) {
                TypeInfo targetInfo = infoByType.get(target);
                CodeCompilationUnit targetUnit = target.getCompilationUnit();
                if (targetInfo == null || targetUnit == null) {
                    continue;
                }
                Located to = new Located(pathOf(targetUnit), targetInfo.localKey());
                if (currentPaths.contains(to.path()) && !to.equals(source) && emitted.add(source + ">" + to)) {
                    out.add(relation(source, to));
                }
            }
        }
        Located file = new Located(artifact.path(), artifact.path());
        for (String imported : unit.getImportedModuleNames()) {
            Located to = resolveImport(imported);
            if (to != null
                    && currentPaths.contains(to.path())
                    && !to.path().equals(artifact.path())
                    && emitted.add(file + ">" + to.path())) {
                out.add(relation(file, new Located(to.path(), to.path())));
            }
        }
    }

    /** The unique type that an import names; trailing segments (static members, nested members) are dropped. */
    private Located resolveImport(String imported) {
        String name = imported;
        while (!name.isEmpty()) {
            List<Located> found = typesByQualifiedName.get(name);
            if (found != null) {
                return found.size() == 1 ? found.get(0) : null;
            }
            int dot = name.lastIndexOf('.');
            name = dot < 0 ? "" : name.substring(0, dot);
        }
        return null;
    }

    private static ObjectNode relation(Located source, Located target) {
        ObjectNode r = JsonMapper.shared().createObjectNode();
        r.put("type", "DEPENDS_ON");
        r.set("source", locator(source));
        r.set("target", locator(target));
        return r;
    }

    private static ObjectNode locator(Located l) {
        ObjectNode o = JsonMapper.shared().createObjectNode();
        o.put("path", l.path());
        o.put("localKey", l.localKey());
        return o;
    }
}
