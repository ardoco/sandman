import cytoscape from "cytoscape";
import { api, Ctx } from "../api";
import { columnOf, columnsLayout, COLUMNS, filterLinks, findingOpen, reviewable, worstSeverity } from "../logic";
import { clear, h, showError } from "./dom";

interface El { id: string; label: string; name?: string; excerpt?: string; parentId?: string | null; artifactId?: string; artifactPath?: string; inferred?: boolean }
interface Link { id: string; sourceId: string; targetId: string; linkType?: string; approach?: string; confidence?: number; status: string; reviewedBy?: string; rationale?: string; reviewReason?: string }
interface Finding { id: string; title: string; severity: string; status: string; type?: string; description?: string; evidence?: unknown; role?: string; involved: { id: string; role?: string }[] }
interface View { elements: El[]; links: Link[]; findings: Finding[]; truncated: boolean }
interface Detail { element: Record<string, unknown>; links: Link[]; findings: Finding[]; neighbours: { relation: string; direction: string; id: string; name?: string; label?: string }[] }

const SEVERITY_COLOR: Record<string, string> = { error: "#b3261e", warning: "#c77d00", info: "#2f5fa7" };
const STATUSES = ["confirmed", "candidate", "rejected"];
const AUTO_LINKED_ONLY = 1000;

export async function renderTraceView(root: HTMLElement, ctx: Ctx): Promise<() => void> {
  const id = ctx.project.id;
  const base = `/v1/projects/${id}/trace-view`;
  const msg = h("div", {});
  const bar = h("div", { class: "row tv-bar" });
  const box = h("div", { class: "graph" });
  const side = h("div", { class: "tv-side" }, h("p", { class: "muted" }, "Select a node or an edge."));
  clear(root, bar, msg, h("div", { class: "tv" }, box, side));

  const cy = cytoscape({
    container: box,
    style: [
      { selector: "node", style: { shape: "round-rectangle", width: 240, height: 44, label: "data(label)", "text-wrap": "wrap", "text-max-width": "220px", "text-valign": "center", "font-size": 10, "background-color": "#4f7cac", color: "#fff", "border-width": 2, "border-color": "data(border)", "border-style": "data(borderStyle)" as never } },
      { selector: "node:parent", style: { label: "data(label)", "text-valign": "top", "background-opacity": 0.08, "background-color": "#888", color: "#888", "border-width": 1, "border-color": "#888", "border-style": "solid" } },
      { selector: "edge", style: { "curve-style": "bezier", width: "data(width)", "line-style": "data(lineStyle)" as never, "line-color": "data(color)", "target-arrow-color": "data(color)", "target-arrow-shape": "triangle", "font-size": 8 } },
      { selector: "edge:selected, edge.hit", style: { label: "data(approach)" } },
      { selector: ".faded", style: { opacity: 0.15 } },
      { selector: ".search", style: { "underlay-color": "#ffd54f", "underlay-opacity": 0.8, "underlay-padding": 4 } },
    ],
  });

  let view: View = { elements: [], links: [], findings: [], truncated: false };
  const cols = COLUMNS.map(() => h("input", { type: "checkbox", checked: true, onchange: () => draw() }));
  const stat = STATUSES.map((s) => h("input", { type: "checkbox", checked: s !== "rejected", onchange: () => draw() }));
  const approach = h("select", { multiple: true, size: 3, onchange: () => draw() });
  const conf = h("input", { type: "number", min: 0, max: 1, step: 0.1, value: "0", onchange: () => draw() });
  const openOnly = h("input", { type: "checkbox", checked: true, onchange: () => draw() });
  const onlyLinked = h("input", { type: "checkbox", onchange: () => draw() });
  const search = h("input", { type: "text", placeholder: "search", oninput: () => mark(true) });
  const lab = (t: string, i: HTMLElement) => h("label", {}, i, t);
  bar.append(...COLUMNS.map((c, i) => lab(c, cols[i])), ...STATUSES.map((s, i) => lab(s, stat[i])),
    h("label", {}, "Approach", approach), h("label", {}, "Min conf.", conf),
    lab("open findings only", openOnly), lab("only linked or flagged", onlyLinked), search);

  const label = (e: El, n: number) => (n ? `[${n}] ` : "") + (e.name || e.excerpt || e.id);

  function draw(): void {
    const keep = new Set(Array.from(approach.selectedOptions).map((o) => o.value));
    const links = filterLinks(view.links, {
      statuses: new Set(STATUSES.filter((_, i) => stat[i].checked)),
      approaches: keep,
      minConfidence: Number(conf.value) || 0,
    });
    const flagged = (eid: string) => view.findings.some((f) => (!openOnly.checked || findingOpen(f.status)) && f.involved.some((i) => i.id === eid));
    const linked = new Set(links.flatMap((l) => [l.sourceId, l.targetId]));
    const shown = view.elements.filter((e) => {
      const c = columnOf(e.label);
      return c >= 0 && cols[c].checked && (!onlyLinked.checked || linked.has(e.id) || flagged(e.id));
    });
    const pos = columnsLayout(shown);
    const ids = new Set(shown.map((e) => e.id));
    const parents = new Map<string, string>();
    for (const e of shown) if (e.artifactId) parents.set(e.artifactId, e.artifactPath ?? e.artifactId);
    cy.batch(() => {
      cy.elements().remove();
      cy.add([...parents].map(([aid, path]) => ({ group: "nodes" as const, data: { id: `a:${aid}`, label: path, border: "#888", borderStyle: "solid" } })));
      cy.add(shown.map((e) => {
        const w = worstSeverity(view.findings, e.id, openOnly.checked);
        const p = pos.get(e.id)!;
        return {
          group: "nodes" as const,
          data: { id: e.id, parent: e.artifactId ? `a:${e.artifactId}` : undefined, label: label(e, w.open), text: `${e.name ?? ""} ${e.excerpt ?? ""}`.toLowerCase(), border: (w.severity && SEVERITY_COLOR[w.severity]) || "#4f7cac", borderStyle: e.inferred ? "dashed" : "solid" },
          position: { x: p.x, y: p.y },
        };
      }));
      const bad = new Set(view.findings.flatMap((f) => (findingOpen(f.status) ? f.involved.map((i) => i.id) : [])));
      cy.add(links.filter((l) => ids.has(l.sourceId) && ids.has(l.targetId)).map((l) => ({
        group: "edges" as const,
        data: { id: l.id, source: l.sourceId, target: l.targetId, approach: l.approach ?? "", width: (1 + 3 * (l.confidence ?? 0)) * (bad.has(l.id) ? 2 : 1), lineStyle: l.status === "confirmed" ? "solid" : l.status === "rejected" ? "dotted" : "dashed", color: bad.has(l.id) ? SEVERITY_COLOR.error : "#8a94a6" },
      })));
    });
    cy.fit(undefined, 20);
    mark(false);
  }

  function mark(fit: boolean): void {
    const q = search.value.trim().toLowerCase();
    cy.nodes().removeClass("search");
    if (!q) return;
    const hit = cy.nodes().filter((n) => !n.isParent() && String(n.data("text")).includes(q));
    hit.addClass("search");
    if (fit && hit.length) cy.fit(hit, 40);
  }

  function focus(sel: cytoscape.Collection): void {
    cy.elements().addClass("faded");
    sel.union(sel.neighborhood()).union(sel.nodes().parent()).removeClass("faded");
  }

  const kv = (k: string, v: unknown) => h("p", {}, h("strong", {}, `${k}: `), typeof v === "string" ? v : JSON.stringify(v));
  const findingsList = (fs: Finding[]) => h("ul", {}, ...fs.map((f) => h("li", {},
    `${f.severity} ${f.status}${f.role ? ` (${f.role})` : ""}: ${f.title}`,
    f.description ? h("div", { class: "muted" }, f.description) : null,
    f.evidence ? h("pre", {}, typeof f.evidence === "string" ? f.evidence : JSON.stringify(f.evidence, null, 2)) : null)));
  const reason = h("input", { type: "text", placeholder: "reason (required to reject)" });

  function linkBlock(l: Link): HTMLElement {
    return h("div", {}, h("p", {}, `${l.approach ?? "?"} ${l.status} conf ${(l.confidence ?? 0).toFixed(2)}${l.reviewedBy ? ` by ${l.reviewedBy}` : ""}`),
      l.rationale ? h("p", { class: "muted" }, l.rationale) : null,
      l.reviewReason ? h("p", { class: "muted" }, `Review: ${l.reviewReason}`) : null,
      reviewable(l.approach ?? "")
        ? h("div", { class: "row" }, ...(["confirmed", "rejected"] as const).map((s) => h("button", { class: "secondary", onclick: () => review(l.id, s) }, s)))
        : h("p", { class: "muted" }, "not reviewable"));
  }

  async function review(linkId: string, to: string): Promise<void> {
    clear(msg);
    const why = reason.value.trim();
    if (to === "rejected" && !why) { showError(msg, "Enter a reason to reject a link."); return; }
    try {
      await api(id, "POST", `/v1/projects/${id}/links/${linkId}/review`, undefined, { status: to, reason: why || undefined });
      await load();
    } catch (e) { showError(msg, e); }
  }

  async function detail(eid: string): Promise<Detail | undefined> {
    try { return await api<Detail>(id, "GET", `${base}/elements/${eid}`); } catch (e) { clear(msg); showError(msg, e); return undefined; }
  }

  async function showNode(eid: string): Promise<void> {
    const d = await detail(eid);
    if (!d) return;
    const el = d.element;
    cy.$id(eid).select();
    clear(side, h("h3", {}, String(el.name ?? el.key ?? eid)),
      kv("kind", el.kind ?? ""), kv("location", `${el.artifactPath ?? el.path ?? ""}${el.startLine ? `:${el.startLine}` : ""}`),
      el.text ? h("pre", {}, String(el.text)) : null,
      h("h4", {}, "Links"), reason, ...(d.links.length ? d.links.map(linkBlock) : [h("p", { class: "muted" }, "none")]),
      h("h4", {}, "Findings"), d.findings.length ? findingsList(d.findings) : h("p", { class: "muted" }, "none"),
      h("h4", {}, "Neighbours"),
      h("ul", {}, ...d.neighbours.map((n) => h("li", { onclick: () => { const t = cy.$id(n.id); if (t.length) focus(t); void showNode(n.id); } },
        `${n.direction === "out" ? "->" : "<-"} ${n.relation} ${n.name ?? n.id}${n.label ? ` (${n.label})` : ""}`))));
  }

  async function showEdge(lid: string): Promise<void> {
    const l = view.links.find((x) => x.id === lid);
    if (!l) return;
    const d = await detail(l.sourceId);
    const full = d?.links.find((x) => x.id === lid) ?? l;
    clear(side, h("h3", {}, "Link"), kv("type", full.linkType ?? ""), reason, linkBlock(full));
  }

  cy.on("tap", "node", (ev) => { if (ev.target.isParent()) return; focus(ev.target); void showNode(ev.target.id()); });
  cy.on("tap", "edge", (ev) => { focus(ev.target.connectedNodes().union(ev.target)); void showEdge(ev.target.id()); });
  cy.on("tap", (ev) => { if (ev.target === cy) cy.elements().removeClass("faded"); });

  async function load(): Promise<void> {
    try {
      view = await api<View>(id, "GET", base);
      const names = [...new Set(view.links.map((l) => l.approach ?? ""))].sort();
      clear(approach, ...names.map((n) => h("option", { value: n }, n || "(none)")));
      if (view.elements.length > AUTO_LINKED_ONLY) onlyLinked.checked = true;
      clear(msg, view.truncated ? h("p", { class: "notice" }, "The server cut the data at its limit. Use the filters to narrow the view.") : null);
      draw();
    } catch (e) { clear(msg); showError(msg, e); }
  }
  await load();
  return () => cy.destroy();
}
