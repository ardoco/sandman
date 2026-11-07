import { api, ApiError, Ctx } from "../api";
import { needsCause } from "../logic";
import { clear, field, fmt, h, jsonView, select, showError, table } from "./dom";

const STATUSES = ["OPEN,REOPENED", "", "OPEN", "DEFERRED", "ACCEPTED", "REJECTED", "RESOLVED", "REOPENED"];
const SEVERITIES = ["", "info", "warning", "error"];
const CAUSES = ["extraction-error", "link-error", "detector-error", "artifact-inconsistency", "planned-change", "intentional"];
const PAGE = "100";

export async function renderFindings(root: HTMLElement, ctx: Ctx): Promise<void> {
  const id = ctx.project.id;
  const status = select(STATUSES, "OPEN,REOPENED");
  const severity = select(SEVERITIES);
  const list = h("div", {});
  const more = h("div", {});
  const detail = h("div", {});
  let items: any[] = [];
  let cursor: string | undefined;
  clear(root, h("div", { class: "row" }, field("Status", status), field("Severity", severity), h("button", { onclick: () => load(true) }, "Apply")), list, more, detail);

  async function load(reset: boolean): Promise<void> {
    try {
      const r = await api(id, "GET", `/v1/projects/${id}/inconsistencies`, {
        status: status.value, severity: severity.value, limit: PAGE, cursor: reset ? undefined : cursor });
      items = reset ? r.items : [...items, ...r.items];
      cursor = r.nextCursor;
      clear(list, items.length
        ? table(["Severity", "Status", "Type", "Title"], items.map((i) => [i.severity, i.status, i.type, i.title]), (n) => show(items[n].id))
        : h("p", { class: "muted" }, "No findings match this filter."));
      clear(more, cursor ? h("button", { class: "secondary", onclick: () => load(false) }, "Load more") : null);
    } catch (e) { clear(list, more); showError(list, e); }
  }

  async function show(fid: string): Promise<void> {
    try {
      const d = await api(id, "GET", `/v1/inconsistencies/${fid}`);
      const reason = h("input", { type: "text", placeholder: "reason (required to accept, reject or defer)" });
      const cause = select(CAUSES);
      const msg = h("div", {});
      const buttons = (d.allowedTransitions as string[]).map((to) => h("button", { class: "secondary", onclick: async () => {
        clear(msg);
        if (needsCause(to) && !reason.value.trim()) { showError(msg, `A reason is required for ${to}.`); return; }
        try {
          await api(id, "POST", `/v1/inconsistencies/${fid}/transitions`, undefined, {
            to, expectedStatus: d.status, reason: reason.value.trim() || undefined, cause: needsCause(to) ? cause.value : undefined });
          await load(true);
          await show(fid);
        } catch (e) {
          showError(msg, e);
          if (e instanceof ApiError && e.status === 409) msg.append(h("button", { onclick: () => show(fid) }, "Reload finding"));
        }
      } }, to));
      clear(detail, h("h3", {}, d.title), h("p", {}, `${d.severity} · ${d.status} · ${d.type} · ${d.detector}`), h("p", {}, d.description),
        ...(d.involved ?? []).map((v: any) => h("div", { class: "involved" }, `${v.role}: ${fmt(v.element?.name ?? v.traceLink?.id)}`,
          v.element ? h("pre", {}, `${v.element.artifactPath}${v.element.startLine ? ":" + v.element.startLine : ""}\n${v.element.excerpt ?? ""}`) : null)),
        d.evidence ? jsonView(d.evidence) : null,
        d.history?.length ? h("details", {}, h("summary", {}, `History (${d.history.length})`),
          ...d.history.map((e: any) => h("div", { class: "muted" }, `${e.at} ${e.actor}: ${fmt(e.from)} to ${e.to}${e.cause ? " [" + e.cause + "]" : ""}${e.reason ? " - " + e.reason : ""}`))) : null,
        h("div", { class: "row" }, reason, cause, ...buttons), msg);
    } catch (e) { clear(detail); showError(detail, e); }
  }
  await load(true);
}
