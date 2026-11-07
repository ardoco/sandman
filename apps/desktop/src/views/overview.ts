import { api, cmd, Ctx, hasToken, saveToken, StackState } from "../api";
import { totalFindings, workflowActive } from "../logic";
import { autoRefresh, clear, h, showError, toast } from "./dom";
import { latestDetail, renderWorkflow } from "./workflows";

function step(done: boolean, title: string, ...body: Array<Node | string | null>): HTMLElement {
  return h("li", { class: done ? "done" : "todo" }, h("strong", {}, `${done ? "Done: " : ""}${title}`), ...body);
}

function tokenSteps(ctx: Ctx, stack: StackState | null, onSaved: () => void): HTMLElement {
  const p = ctx.project;
  const local = p.mode === "local";
  const msg = h("div", {});
  const open = (path: string) => cmd("open_external", { url: p.url.replace(/\/$/, "") + path }).catch((e) => showError(msg, e));
  const token = h("input", { type: "password", autocomplete: "off", placeholder: "Paste the token" });
  const check = h("button", { onclick: async () => {
    clear(msg);
    if (!token.value.trim()) return showError(msg, "Paste a token first.");
    check.disabled = true;
    try {
      const r = await saveToken(p.id, token.value.trim());
      if (r.ok) { token.value = ""; toast(r.message); onSaved(); return; }
      showError(msg, r.message);
    } catch (e) { showError(msg, e); }
    check.disabled = false;
  } }, "Save and check");
  const copy = h("button", { class: "secondary", onclick: async () => {
    clear(msg);
    try {
      await navigator.clipboard.writeText(await cmd<string>("admin_password", { projectId: p.id }));
      toast("Password copied to the clipboard.");
    } catch (e) { showError(msg, e); }
  } }, "Copy admin password");
  const running = stack?.state === "running";
  return h("div", { class: "notice" }, h("strong", {}, "No token yet. Follow these steps."), h("ol", { class: "steps" },
    ...(local ? [
      step(running, "Start the stack.", h("span", { class: "muted" }, running ? " The stack runs." : ` State: ${stack?.state ?? "unknown"}. Use Start above.`)),
      step(false, "Find the admin password.", h("div", { class: "muted" }, "File: deploy/secrets/admin_bootstrap_password. User: admin."), copy),
      step(false, "Sign in and change the password.", h("div", {}, h("button", { class: "secondary", onclick: () => open("/console/login") }, "Open console login"))),
    ] : []),
    step(false, "Create a token.", h("div", {}, h("button", { class: "secondary", onclick: () => open("/console/tokens") }, "Open console tokens page"))),
    step(false, "Paste the token.", h("div", { class: "row" }, token, check))), msg);
}

export async function renderOverview(root: HTMLElement, ctx: Ctx, stack: StackState | null): Promise<() => void> {
  const p = ctx.project;
  const top = h("p", {}, `System: ${stack ? stack.state : "unknown"}${stack?.ready ? " (ready)" : ""}`);
  const body = h("div", {});
  clear(root, top, body);
  if (!(await hasToken(p.id))) {
    clear(body, tokenSteps(ctx, stack, () => void renderOverview(root, ctx, stack)));
    return () => undefined;
  }
  async function load(): Promise<boolean> {
    try {
      const f = await api(p.id, "GET", `/v1/projects/${p.id}/inconsistencies`, { limit: "1" });
      const counts: Record<string, number> = f.counts ?? {};
      const wf = await latestDetail(p.id);
      clear(body, h("h3", {}, `Findings (${totalFindings(counts)})`),
        totalFindings(counts) ? h("ul", {}, ...Object.entries(counts).map(([k, v]) => h("li", {}, `${k}: ${v}`))) : h("p", { class: "muted" }, "No findings yet."),
        h("h3", {}, "Latest workflow"), wf ? renderWorkflow(wf) : h("p", { class: "muted" }, "None yet. Use Ingest."));
      return !!wf && workflowActive(wf.status);
    } catch (e) { clear(body); showError(body, e); return false; }
  }
  return autoRefresh(load);
}
