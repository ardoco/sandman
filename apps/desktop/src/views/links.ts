import { api, Ctx, cypher } from "../api";
import { clear, fmt, h, select, showError, table } from "./dom";

export async function renderLinks(root: HTMLElement, ctx: Ctx): Promise<void> {
  const id = ctx.project.id;
  const status = select(["", "candidate", "confirmed", "rejected"]);
  const approach = h("input", { type: "text", placeholder: "approach" });
  const list = h("div", {});
  const msg = h("div", {});
  clear(root, h("div", { class: "row" }, status, approach, h("button", { onclick: load }, "Apply")), msg, list);
  async function load(): Promise<void> {
    clear(msg);
    const where = ["l.active = true"];
    const params: Record<string, unknown> = { projectId: id };
    if (status.value) { where.push("l.status = $status"); params.status = status.value; }
    if (approach.value) { where.push("l.approach = $approach"); params.approach = approach.value; }
    try {
      const r = await cypher(id,
        `MATCH (l:TraceLink {projectId: $projectId})-[:SOURCE]->(s:Element {projectId: $projectId}) MATCH (l)-[:TARGET]->(t:Element {projectId: $projectId}) WHERE ${where.join(" AND ")} RETURN l.id AS id, l.status AS status, l.approach AS approach, l.linkType AS linkType, l.confidence AS confidence, s.name AS source, t.name AS target ORDER BY l.confidence DESC`,
        params);
      const rows = r.rows;
      clear(list, table(["Status", "Approach", "Type", "Conf.", "Source", "Target", ""], rows.map((row) => [
        fmt(row[1]), fmt(row[2]), fmt(row[3]), Number(row[4]).toFixed(2), fmt(row[5]), fmt(row[6]),
        h("span", {}, ...(["confirmed", "rejected"] as const).map((s) => h("button", { class: "secondary", onclick: () => review(String(row[0]), s) }, s)))])));
    } catch (e) { clear(list); showError(list, e); }
  }
  async function review(linkId: string, to: string): Promise<void> {
    const reason = to === "rejected" ? prompt("Reason for rejection") : undefined;
    if (to === "rejected" && !reason) return;
    try {
      await api(id, "POST", `/v1/projects/${id}/links/${linkId}/review`, undefined, { status: to, reason: reason || undefined });
      await load();
    } catch (e) { showError(msg, e); }
  }
  await load();
}
