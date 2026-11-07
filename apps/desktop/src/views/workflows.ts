import { api, Ctx } from "../api";
import { latestWorkflow, workflowActive, workflowProgress } from "../logic";
import { autoRefresh, clear, h, showError, table } from "./dom";

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
  const id = ctx.project.id;
  const detail = h("div", {});
  const body = h("div", {});
  const more = h("div", {});
  const types = ["full", "extract", "tlr", "detect"];
  const type = h("select", {}, ...types.map((t) => h("option", { value: t }, t)));
  const msg = h("div", {});
  let items: any[] = [];
  let cursor: string | undefined;
  let selected: string | undefined;
  let pages = 1;
  clear(root, h("div", { class: "row" }, type, h("button", { onclick: async () => {
    clear(msg);
    try {
      const wf = await api(id, "POST", `/v1/projects/${id}/workflows`, undefined, { type: type.value });
      selected = wf.id;
      refresh.kick();
    } catch (e) { showError(msg, e); }
  } }, "Run workflow"), h("button", { class: "secondary", onclick: () => refresh.kick() }, "Refresh")), msg, body, more, detail);

  async function load(): Promise<boolean> {
    try {
      let list = await api(id, "GET", `/v1/projects/${id}/workflows`, { limit: "20" });
      items = list.items;
      cursor = list.nextCursor;
      for (let n = 1; n < pages && cursor; n++) {
        list = await api(id, "GET", `/v1/projects/${id}/workflows`, { limit: "20", cursor });
        items = [...items, ...list.items];
        cursor = list.nextCursor;
      }
      draw();
      const current = selected ? items.find((w) => w.id === selected) : undefined;
      if (selected) clear(detail, renderWorkflow(await api(id, "GET", `/v1/workflows/${selected}`)));
      return items.some((w) => workflowActive(w.status)) || (!!current && workflowActive(current.status));
    } catch (e) { clear(body, more); showError(body, e); return false; }
  }
  function draw(): void {
    clear(body, items.length
      ? table(["Id", "Status", "Trigger", "Created"], items.map((w) => [w.id, w.status, w.trigger?.kind, w.createdAt]), (i) => { selected = items[i].id; refresh.kick(); })
      : h("p", { class: "muted" }, "No workflow yet. Run one or ingest files."));
    clear(more, cursor ? h("button", { class: "secondary", onclick: async () => {
      const next = await api(id, "GET", `/v1/projects/${id}/workflows`, { limit: "20", cursor }).catch((e) => { showError(msg, e); return undefined; });
      if (!next) return;
      items = [...items, ...next.items];
      cursor = next.nextCursor;
      pages += 1;
      draw();
    } }, "Load more") : null);
  }
  const refresh = autoRefresh(load);
  return refresh;
}
