import cytoscape from "cytoscape";
import { Ctx, cypher } from "../api";
import { clear, h, showError } from "./dom";

export async function renderGraph(root: HTMLElement, ctx: Ctx): Promise<() => void> {
  const id = ctx.project.id;
  const box = h("div", { class: "graph" });
  const msg = h("div", {});
  clear(root, msg, box);
  const cy = cytoscape({
    container: box,
    style: [
      { selector: "node", style: { label: "data(label)", "font-size": 10, "background-color": "#4f7cac" } },
      { selector: "edge", style: { label: "data(type)", "font-size": 8, width: 1, "curve-style": "bezier", "target-arrow-shape": "triangle" } },
    ],
    layout: { name: "cose" },
  });
  const seen = new Set<string>();
  async function expand(elementId: string | null): Promise<void> {
    try {
      const r = await cypher(id,
        elementId
          ? `MATCH (a:Element {id: $eid, projectId: $projectId})-[r]-(b:Element {projectId: $projectId}) RETURN a.id, a.name, type(r), b.id, b.name LIMIT 100`
          : `MATCH (a:Element {projectId: $projectId})-[r]->(b:Element {projectId: $projectId}) RETURN a.id, a.name, type(r), b.id, b.name LIMIT 100`,
        elementId ? { eid: elementId } : {});
      for (const [a, an, t, b, bn] of r.rows as string[][]) {
        for (const [nid, name] of [[a, an], [b, bn]]) if (!cy.getElementById(nid).length) cy.add({ data: { id: nid, label: name } });
        const eid = `${a}|${t}|${b}`;
        if (!seen.has(eid)) { seen.add(eid); cy.add({ data: { id: eid, source: a, target: b, type: t } }); }
      }
      cy.layout({ name: "cose" }).run();
    } catch (e) { showError(msg, e); }
  }
  cy.on("tap", "node", (ev) => expand(ev.target.id()));
  await expand(null);
  return () => cy.destroy();
}
