"""Generate the component site (site/dist: index.html + components.json) from repo sources."""

import ast
import json
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]
REPO = "https://github.com/ardoco/sandman"
GROUPS = ["platform", "agents", "plugins", "infrastructure", "libraries", "apps"]
DOC_FILES = ["docs/plugins.md"]
DOC_GLOBS = ["adr/*.md"]
MAX_PER_DOC = 5
META = ROOT / "site/src/components.yaml"
MAX_FILES = 60
TEXT_FIELDS = ("zone", "title", "summary", "details", "flow")


def url(path: str, anchor: str = "") -> str:
    kind = "tree" if (ROOT / path).is_dir() else "blob"
    return f"{REPO}/{kind}/main/{path}" + (f"#{anchor}" if anchor else "")


def link(label: str, path: str, anchor: str = "") -> dict:
    return {"label": label, "path": path, "url": url(path, anchor)}


def first_paragraph(text: str) -> str:
    return " ".join(text.strip().split("\n\n")[0].split())


def slugs(headings: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    out = []
    for h in headings:
        s = re.sub(r"[^\w\- ]", "", h.lower()).replace(" ", "-")
        n = seen.get(s, 0)
        seen[s] = n + 1
        out.append(s if n == 0 else f"{s}-{n}")
    return out


def sections(path: Path) -> tuple[str, list[tuple[str, str, str]]]:
    """Return (title, [(heading, anchor, body)]) for headings up to level 3, skipping code fences."""
    title, heads, bodies, fence = "", [], [], False
    for line in path.read_text().splitlines():
        if line.startswith("```"):
            fence = not fence
        m = None if fence else re.match(r"(#{1,3}) +(.*?)\s*$", line)
        if m:
            if not title and len(m.group(1)) == 1:
                title = m.group(2)
            heads.append(m.group(2))
            bodies.append([])
        elif bodies:
            bodies[-1].append(line)
    return title or path.stem, [(h, a, "\n".join(b)) for h, a, b in zip(heads, slugs(heads), bodies, strict=True)]


def mentions(name: str, text: str) -> int:
    return len(re.findall(rf"(?<![\w-]){re.escape(name)}(?![\w-])", text))


def doc_links(name: str) -> list[dict]:
    links = []
    for rel in DOC_FILES:
        title, secs = sections(ROOT / rel)
        hits = [(mentions(name, h + "\n" + b), h, a) for h, a, b in secs]
        hits = sorted((x for x in hits if x[0]), key=lambda x: (-x[0], x[2]))[:MAX_PER_DOC]
        links += [link(f"{Path(rel).stem}: {h}", rel, a) for _, h, a in sorted(hits, key=lambda x: x[2])]
    for pattern in DOC_GLOBS:
        hits = []
        for p in sorted(ROOT.glob(pattern)):
            n = mentions(name, p.read_text())
            if n:
                hits.append((n, p.relative_to(ROOT).as_posix(), sections(p)[0]))
        hits = sorted(hits, key=lambda x: (-x[0], x[1]))[:MAX_PER_DOC]
        links += [link(t, rel) for _, rel, t in sorted(hits, key=lambda x: x[1])]
    return links


def compose_comments(text: str) -> dict[str, str]:
    out = {}
    for m in re.finditer(r"^  ([a-z][\w-]*):[ \t]*#[ \t]*(.+)$", text, re.M):
        out[m.group(1)] = m.group(2).strip()
    return out


def describe_dir(d: Path) -> str:
    readme = d / "README.md"
    if readme.exists():
        body = "\n".join(line for line in readme.read_text().splitlines() if not line.startswith("#"))
        if body.strip():
            return first_paragraph(body)
    if (d / "pyproject.toml").exists():
        m = re.search(r'^description\s*=\s*"(.*)"', (d / "pyproject.toml").read_text(), re.M)
        if m:
            return m.group(1)
    if (d / "pom.xml").exists():
        name = ET.parse(d / "pom.xml").getroot().find("{*}name")
        if name is not None and name.text:
            return name.text.strip()
    return ""


def classify(name: str, svc: dict) -> str:
    if name.startswith("plugin"):
        return "plugins"
    if name.endswith("-agent") or name == "orchestrator":
        return "agents"
    return "platform" if "build" in svc else "infrastructure"


def strings(node, path=""):
    if isinstance(node, str):
        yield path, node
    elif isinstance(node, dict):
        for k, v in node.items():
            yield from strings(v, f"{path}.{k}" if path else k)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from strings(v, f"{path}[{i}]")


def milestones() -> list[dict]:
    """Milestones (id, name, goal) from components.yaml."""
    return yaml.safe_load(META.read_text())["milestones"]


def status_line(comps: dict, edges: dict) -> str:
    return f"Status: {len(comps)} components and {len(edges)} connections in this repository."


def sync_problems(ids: set[str], meta: dict, ms: list[dict] | None = None) -> list[str]:
    """Return mismatches between components found in the repo and the human-written components.yaml."""
    ms = milestones() if ms is None else ms
    texts = meta["components"]
    problems = [f"no entry in components.yaml: {i}" for i in sorted(ids - set(texts))]
    problems += [f"components.yaml entry without component: {i}" for i in sorted(set(texts) - ids)]
    for i, t in texts.items():
        problems += [f"{i}: missing {f}" for f in TEXT_FIELDS if not str(t.get(f, "")).strip()]
        if t.get("zone") not in meta["zones"]:
            problems.append(f"{i}: unknown zone {t.get('zone')}")
    byid = {m["id"]: m for m in ms}
    for i, t in texts.items():
        if "planned" in t and t["planned"] not in byid:
            problems.append(f"{i}: planned milestone {t['planned']} is not in the milestones list")
    for path, text in strings({k: v for k, v in meta.items() if k != "milestones"}):
        if re.search(r"\bM\d+\b|planned", text, re.I) and not path.endswith(".planned"):
            problems.append(f"{path}: milestone or 'planned' text in YAML, use the planned field")
    for step in (s for f in meta["flows"] for s in f["steps"]):
        problems += [f"flow step {step['title']}: unknown component {i}" for i in step["components"] if i not in ids]
    return problems


def repo_files() -> list[str]:
    try:
        out = subprocess.run(
            ["git", "ls-files", "-co", "--exclude-standard"], cwd=ROOT, capture_output=True, text=True, check=True
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return sorted(
            p.relative_to(ROOT).as_posix() for p in ROOT.rglob("*") if p.is_file() and "target" not in p.parts
        )
    return sorted(set(out.splitlines()))


def file_role(rel: str) -> str | None:
    parts = rel.split("/")
    if "test" in parts[:-1] or "tests" in parts[:-1]:
        return "tests"
    if "src" in parts[:-1] and "__pycache__" not in parts:
        return "source"
    if parts[-1] in ("Dockerfile", "pyproject.toml", "pom.xml") or parts[-1].endswith(
        (".toml", ".yaml", ".yml", ".json")
    ):
        return "config"
    return "docs" if parts[-1].endswith(".md") and parts[-1] != "THIRD-PARTY.md" else None


def file_entry(path: str, root: str, **extra) -> dict:
    rel = path[len(root) :].lstrip("/") if root and path.startswith(root) else path
    kind = Path(path).suffix.lstrip(".") or Path(path).name.lower()
    return {"path": path, "url": url(path), "kind": kind, "dir": "/".join(rel.split("/")[:-1][:2]) or ".", **extra}


def test_count(path: str) -> int:
    text = (ROOT / path).read_text(errors="replace")
    return len(re.findall(r"def test_|@Test\b", text))


def python_modules(sources: list[str]) -> list[dict]:
    out = []
    for p in sources:
        if not p.endswith(".py") or p.endswith("__init__.py") or "/src/" not in p:
            continue
        try:
            doc = ast.get_docstring(ast.parse((ROOT / p).read_text())) or ""
        except (SyntaxError, ValueError):
            doc = ""
        mod = Path(p).relative_to(p.split("/src/")[0] + "/src").with_suffix("").as_posix().replace("/", ".")
        out.append({"kind": "module", "name": mod, "description": doc.strip().split("\n")[0], "path": p, "url": url(p)})
    return out


def internals(c: dict, name: str, roots: list[str], allfiles: list[str]) -> dict:
    groups: dict[str, list[dict]] = {k: [] for k in ("source", "tests", "config", "docs")}
    srcs = []
    for root in roots:
        for p in allfiles:
            if not p.startswith(root + "/"):
                continue
            role = file_role(p[len(root) + 1 :])
            if role:
                groups[role].append(
                    file_entry(
                        p,
                        root,
                        **({"count": test_count(p)} if role == "tests" and p.endswith((".py", ".java")) else {}),
                    )
                )
            if role == "source":
                srcs.append(p)
    features = []
    card = ROOT / f"contracts/agent-cards/{name}.json"
    spec = ROOT / f"contracts/openapi/{name}.yaml"
    if card.exists():
        cp = card.relative_to(ROOT).as_posix()
        features += [
            {
                "kind": "skill",
                "name": s.get("name") or s.get("id", ""),
                "description": first_paragraph(s.get("description", "")),
                "path": cp,
                "url": url(cp),
            }
            for s in json.loads(card.read_text()).get("skills", [])
        ]
        groups["config"].append(file_entry(cp, "", kind="agent-card"))
    if spec.exists():
        sp = spec.relative_to(ROOT).as_posix()
        for route, ops in (yaml.safe_load(spec.read_text()).get("paths") or {}).items():
            for method, op in sorted((ops or {}).items()):
                if method in ("get", "put", "post", "delete", "patch", "head", "options") and isinstance(op, dict):
                    features.append(
                        {
                            "kind": "operation",
                            "name": f"{method.upper()} {route}",
                            "description": " ".join(str(op.get("summary", "")).split()),
                            "path": sp,
                            "url": url(sp),
                        }
                    )
        groups["config"].append(file_entry(sp, "", kind="openapi"))
    groups["config"].append(file_entry("deploy/docker-compose.yml", "", kind="compose"))
    if name.startswith("plugin"):
        groups["docs"].append(file_entry("docs/plugins.md", "", kind="md"))
    seen = {e["path"] for e in groups["docs"]}
    for ln in c["links"]:
        if re.match(r"(docs|adr)/", ln["path"]) and ln["path"] not in seen:
            seen.add(ln["path"])
            groups["docs"].append(file_entry(ln["path"], "", kind="md"))
    features += python_modules(srcs)
    features.sort(key=lambda f: (f["kind"], f["name"]))
    tests = sorted(groups["tests"], key=lambda e: e["path"])
    truncated = {}
    for k, v in groups.items():
        v.sort(key=lambda e: e["path"])
        if len(v) > MAX_FILES:
            truncated[k] = len(v) - MAX_FILES
            groups[k] = v[:MAX_FILES]
    return {
        "roots": roots,
        "files": groups,
        "features": features,
        "tests": [e for e in tests if "count" in e][:MAX_FILES],
        "testCount": sum(e.get("count", 0) for e in tests),
        "truncated": truncated,
        "compose": {"image": c["image"], "profiles": c["profiles"], "networks": c["networks"]},
    }


def build() -> dict:
    compose_path = ROOT / "deploy/docker-compose.yml"
    compose = yaml.safe_load(compose_path.read_text())
    comments = compose_comments(compose_path.read_text())
    services = compose["services"]
    comps: dict[str, dict] = {}

    for name, svc in services.items():
        c = {"id": name, "name": name, "group": classify(name, svc), "kind": "service", "links": []}
        c["profiles"] = svc.get("profiles", [])
        c["networks"] = svc.get("networks", [])
        c["image"] = svc["image"].split("@")[0] if "image" in svc else None
        desc = ""
        card = ROOT / f"contracts/agent-cards/{name}.json"
        spec = ROOT / f"contracts/openapi/{name}.yaml"
        src = ROOT / "services" / name
        if card.exists():
            desc = json.loads(card.read_text()).get("description", "")
            c["links"].append(link("Agent card", f"contracts/agent-cards/{name}.json"))
        if spec.exists():
            info = yaml.safe_load(spec.read_text())["info"]
            desc = desc or first_paragraph(info.get("description", "")) or info.get("title", "")
            c["links"].append(link(f"OpenAPI: {info.get('title', name)}", f"contracts/openapi/{name}.yaml"))
        if src.is_dir():
            desc = desc or describe_dir(src)
            c["links"].append(link("Source", f"services/{name}"))
            for f in ("README.md", "Dockerfile"):
                if (src / f).exists():
                    c["links"].append(link(f, f"services/{name}/{f}"))
        if name.startswith("plugin") and (ROOT / "tests" / name).is_dir():
            desc = desc or describe_dir(ROOT / "tests" / name)
        c["description"] = desc or comments.get(name) or (f"Container image {c['image']}." if c["image"] else name)
        c["links"].append(link("Compose definition", "deploy/docker-compose.yml"))
        if name.startswith("plugin"):
            c["links"].append(link("Plugin guide", "docs/plugins.md"))
            for t in sorted((ROOT / "contracts/agent-cards").glob("plugin-*.template.json")):
                c["links"].append(link(t.name, t.relative_to(ROOT).as_posix()))
        comps[name] = c

    for lang in ("java", "python"):
        for d in sorted((ROOT / "libs" / lang).iterdir()):
            if not d.is_dir():
                continue
            rel = d.relative_to(ROOT).as_posix()
            c = {
                "id": d.name,
                "name": d.name,
                "group": "libraries",
                "kind": f"{lang} library",
                "profiles": [],
                "networks": [],
                "image": None,
                "description": describe_dir(d) or d.name,
                "links": [link("Source", rel)],
            }
            for f in ("README.md", "pyproject.toml", "pom.xml"):
                if (d / f).exists():
                    c["links"].append(link(f, f"{rel}/{f}"))
            comps[d.name] = c

    for d in sorted((ROOT / "apps").iterdir()):
        if not d.is_dir():
            continue
        rel = d.relative_to(ROOT).as_posix()
        comps[d.name] = {
            "id": d.name,
            "name": d.name,
            "group": "apps",
            "kind": "desktop app",
            "profiles": [],
            "networks": [],
            "image": None,
            "description": describe_dir(d) or d.name,
            "links": [
                link("Source", rel),
                link("README.md", f"{rel}/README.md"),
                link("ADR-0019: Desktop application", "adr/0019-desktop-app.md"),
                link("User guide", "docs/user/desktop-app.md"),
            ],
        }

    allfiles = repo_files()
    for c in comps.values():
        c["links"] += doc_links(c["name"])
        n = c["name"]
        roots = [r for r in (f"services/{n}", f"tests/{n}") if any(p.startswith(r + "/") for p in allfiles)]
        if c["kind"] != "service":
            roots = [c["links"][0]["path"]]
        c["internals"] = internals(c, n, roots, allfiles)

    edges: dict[tuple[str, str], str] = {}
    for name, svc in services.items():
        deps = svc.get("depends_on", [])
        for dep in deps:
            edges[(name, dep)] = "depends"
        env = svc.get("environment", {})
        env = env if isinstance(env, dict) else dict(e.split("=", 1) for e in env if "=" in e)
        for v in env.values():
            for target in re.findall(r"://([a-z][\w-]*):\d+", str(v)):
                if target in services and target != name:
                    edges[(name, target)] = "calls"
    for name, dep in edges:
        comps[name].setdefault("dependsOn", []).append(dep)
        comps[dep].setdefault("usedBy", []).append(name)
    for c in comps.values():
        c["dependsOn"] = sorted(c.get("dependsOn", []))
        c["usedBy"] = sorted(c.get("usedBy", []))

    meta = yaml.safe_load(META.read_text())
    ms = milestones()
    if problems := sync_problems(set(comps), meta, ms):
        raise SystemExit("site/src/components.yaml out of sync:\n  " + "\n  ".join(problems))
    order = list(meta["components"])
    for i, c in comps.items():
        c.update({f: " ".join(meta["components"][i][f].split()) for f in TEXT_FIELDS})
        if "planned" in meta["components"][i]:
            c["planned"] = meta["components"][i]["planned"]

    return {
        "repo": REPO,
        "intro": meta["intro"],
        "status": status_line(comps, edges),
        "milestones": ms,
        "flows": meta["flows"],
        "glossary": meta["glossary"],
        "zones": [{"id": k, **v} for k, v in meta["zones"].items()],
        "groups": GROUPS,
        "components": sorted(comps.values(), key=lambda c: order.index(c["id"])),
        "edges": [{"from": a, "to": b, "kind": k} for (a, b), k in sorted(edges.items())],
    }


def main(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    data = build()
    (out / "components.json").write_text(json.dumps(data, indent=1) + "\n")
    shutil.copy(ROOT / "site/src/index.html", out / "index.html")
    shutil.copytree(ROOT / "site/src/fonts", out / "fonts", dirs_exist_ok=True)
    print(f"{len(data['components'])} components, {len(data['edges'])} edges -> {out}")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "site/dist")
