import { api, Ctx } from "../api";
import { latestWorkflow, workflowProgress } from "../logic";
import { clear, h, showError, table } from "./dom";

export function renderWorkflow(wf: any): HTMLElement {
  const p = workflowProgress(wf);
  return h("div", { class: "workflow" },
    h("div", {}, `${wf.id} · ${wf.status} · ${p.done}/${p.total} steps`),
    h("progress", { max: "100", value: String(p.percent) }),
    h("div", { class: "muted" }, p.label),
    ...(wf.steps ?? []).map((s: any) => h("div", { class: `step ${String(s.status).toLowerCase()}` }, `${s.status} ${s.skill} (${s.agent}) x${s.attempts}`)));
}

export async function latestDetail(projectId: string): Promise<any | undefined> {
  const list = await api(projectId, "GET", `/v1/projects/${projectId}/workflows`, { limit: "20" });
  const wf = latestWorkflow<any>(list.items);
  return wf ? api(projectId, "GET", `/v1/workflows/${wf.id}`) : undefined;
}

export async function renderWorkflows(root: HTMLElement, ctx: Ctx): Promise<() => void> {
  const detail = h("div", {});
  const body = h("div", {});
  const types = ["full", "extract", "tlr", "detect"];
  const type = h("select", {}, ...types.map((t) => h("option", { value: t }, t)));
  const msg = h("div", {});
  clear(root, h("div", { class: "row" }, type, h("button", { onclick: async () => {
    clear(msg);
    try {
      await api(ctx.project.id, "POST", `/v1/projects/${ctx.project.id}/workflows`, undefined, { type: type.value });
      load();
    } catch (e) { showError(msg, e); }
  } }, "Run workflow"), h("button", { class: "secondary", onclick: () => load() }, "Refresh")), msg, body, detail);
  async function load(): Promise<void> {
    try {
      const list = await api(ctx.project.id, "GET", `/v1/projects/${ctx.project.id}/workflows`, { limit: "50" });
      const items: any[] = [...list.items].sort((a, b) => b.createdAt.localeCompare(a.createdAt));
      clear(body, table(["Id", "Status", "Trigger", "Created"], items.map((w) => [w.id, w.status, w.trigger?.kind, w.createdAt]), async (i) => {
        clear(detail, renderWorkflow(await api(ctx.project.id, "GET", `/v1/workflows/${items[i].id}`)));
      }));
    } catch (e) { clear(body); showError(body, e); }
  }
  await load();
  const t = setInterval(load, 5000);
  return () => clearInterval(t);
}
