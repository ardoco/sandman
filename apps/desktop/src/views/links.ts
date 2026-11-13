import { Key, t } from "../i18n";
import { api, Ctx, cypher } from "../api";
import { reviewable } from "../logic";
import { clear, field, fmt, h, select, showError, table } from "./dom";

const LINK_STATUSES = ["candidate", "confirmed", "rejected"];
const STEP = 200;
const MAX = 1000;

export async function renderLinks(root: HTMLElement, ctx: Ctx): Promise<void> {
  const id = ctx.project.id;
  const status = select(["", ...LINK_STATUSES].map((s): [string, string] => [s, s && t(`common.${s}` as Key)]));
  const approach = h("input", { type: "text", placeholder: t("links.approachPlaceholder") });
  const reason = h("input", { type: "text", placeholder: t("links.reasonPlaceholder") });
  const list = h("div", {});
  const more = h("div", {});
  const msg = h("div", {});
  let limit = STEP;
  clear(root, h("div", { class: "row" }, field(t("common.status"), status), field(t("common.approach"), approach), field(t("common.reason"), reason),
    h("button", { onclick: () => { limit = STEP; load(); } }, t("common.apply"))), msg, list, more);

  async function load(): Promise<void> {
    const where = ["l.active = true"];
    const params: Record<string, unknown> = {};
    if (status.value) { where.push("l.status = $status"); params.status = status.value; }
    if (approach.value) { where.push("l.approach = $approach"); params.approach = approach.value; }
    try {
      const r = await cypher(id,
        `MATCH (l:TraceLink {projectId: $projectId})-[:SOURCE]->(s:Element {projectId: $projectId}) MATCH (l)-[:TARGET]->(t:Element {projectId: $projectId}) WHERE ${where.join(" AND ")} RETURN l.id AS id, l.status AS status, l.approach AS approach, l.linkType AS linkType, l.confidence AS confidence, s.name AS source, t.name AS target ORDER BY l.confidence DESC, l.id`,
        params, limit);
      clear(list, r.rows.length
        ? table([t("common.status"), t("common.approach"), t("links.colType"), t("links.colConf"), t("links.colSource"), t("links.colTarget"), ""], r.rows.map((row) => [
          fmt(row[1]), fmt(row[2]), fmt(row[3]), Number(row[4]).toFixed(2), fmt(row[5]), fmt(row[6]),
          reviewable(String(row[2]))
            ? h("span", {}, ...(["confirmed", "rejected"] as const).map((s) => h("button", { class: "secondary", onclick: () => review(String(row[0]), s) }, t(`common.${s}`))))
            : h("span", { class: "muted" }, t("links.notReviewable"))]))
        : h("p", { class: "muted" }, t("links.none")));
      clear(more, r.truncated && limit < MAX ? h("button", { class: "secondary", onclick: () => { limit = Math.min(limit + STEP, MAX); load(); } }, t("common.loadMore"))
        : r.truncated ? h("p", { class: "muted" }, t("links.truncated", { max: MAX })) : null);
    } catch (e) { clear(list, more); showError(list, e); }
  }
  async function review(linkId: string, to: string): Promise<void> {
    clear(msg);
    const why = reason.value.trim();
    if (to === "rejected" && !why) { showError(msg, t("links.reasonNeeded")); return; }
    try {
      await api(id, "POST", `/v1/projects/${id}/links/${linkId}/review`, undefined, { status: to, reason: why || undefined });
      await load();
    } catch (e) { showError(msg, e); }
  }
  await load();
}
