import { api, Ctx } from "../api";
import { needsCause } from "../logic";
import { clear, field, fmt, h, jsonView, select, showError, table } from "./dom";

const STATUSES = ["", "OPEN", "DEFERRED", "ACCEPTED", "REJECTED", "RESOLVED", "REOPENED"];
const SEVERITIES = ["", "info", "warning", "error"];
const CAUSES = ["extraction-error", "link-error", "detector-error", "artifact-inconsistency", "planned-change", "intentional"];

export async function renderFindings(root: HTMLElement, ctx: Ctx): Promise<void> {
  const id = ctx.project.id;
  const status = select(STATUSES, "OPEN");
  const severity = select(SEVERITIES);
  const list = h("div", {});
  const detail = h("div", {});
  clear(root, h("div", { class: "row" }, field("Status", status), field("Severity", severity), h("button", { onclick: load }, "Apply")), list, detail);
  async function load(): Promise<void> {
    try {
      const r = await api(id, "GET", `/v1/projects/${id}/inconsistencies`, { status: status.value, severity: severity.value, limit: "200" });
      const items: any[] = r.items;
      clear(list, table(["Severity", "Status", "Type", "Title"], items.map((i) => [i.severity, i.status, i.type, i.title]), (n) => show(items[n].id)));
    } catch (e) { clear(list); showError(list, e); }
  }
  async function show(fid: string): Promise<void> {
    try {
      const d = await api(id, "GET", `/v1/inconsistencies/${fid}`);
      const reason = h("input", { type: "text", placeholder: "reason" });
      const cause = select(CAUSES);
      const msg = h("div", {});
      const buttons = (d.allowedTransitions as string[]).map((to) => h("button", { class: "secondary", onclick: async () => {
        clear(msg);
        try {
          await api(id, "POST", `/v1/inconsistencies/${fid}/transitions`, undefined, {
            to, expectedStatus: d.status, reason: reason.value || undefined, cause: needsCause(to) ? cause.value : undefined });
          await load();
          await show(fid);
        } catch (e) { showError(msg, e); }
      } }, to));
      clear(detail, h("h3", {}, d.title), h("p", {}, `${d.severity} · ${d.status} · ${d.type} · ${d.detector}`), h("p", {}, d.description),
        ...(d.involved ?? []).map((v: any) => h("div", { class: "involved" }, `${v.role}: ${fmt(v.element?.name ?? v.traceLink?.id)}`,
          v.element ? h("pre", {}, `${v.element.artifactPath}${v.element.startLine ? ":" + v.element.startLine : ""}\n${v.element.excerpt ?? ""}`) : null)),
        d.evidence ? jsonView(d.evidence) : null,
        h("div", { class: "row" }, reason, cause, ...buttons), msg);
    } catch (e) { clear(detail); showError(detail, e); }
  }
  await load();
}
