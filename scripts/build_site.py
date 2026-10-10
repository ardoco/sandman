"""Generate the component site (site/dist: index.html + components.json) from repo sources."""

import json
import re
import shutil
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]
REPO = "https://github.com/ardoco/sandman"
GROUPS = ["platform", "agents", "plugins", "infrastructure", "libraries"]
DOC_FILES = ["docs/stage-1-analysis-architecture.md", "docs/stage-2-contracts-repository.md", "docs/plugins.md"]
DOC_GLOBS = ["docs/tasks/*.md", "adr/*.md"]
MAX_PER_DOC = 5


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

    for c in comps.values():
        c["links"] += doc_links(c["name"])

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

    return {
        "repo": REPO,
        "groups": GROUPS,
        "components": sorted(comps.values(), key=lambda c: (GROUPS.index(c["group"]), c["id"])),
        "edges": [{"from": a, "to": b, "kind": k} for (a, b), k in sorted(edges.items())],
    }


def main(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    data = build()
    (out / "components.json").write_text(json.dumps(data, indent=1) + "\n")
    shutil.copy(ROOT / "site/src/index.html", out / "index.html")
    print(f"{len(data['components'])} components, {len(data['edges'])} edges -> {out}")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "site/dist")
