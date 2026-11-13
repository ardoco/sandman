import { t } from "../i18n";
import { api, cmd, Ctx, hasToken, saveToken, StackState } from "../api";
import { stackLabel, totalFindings, workflowActive } from "../logic";
import { autoRefresh, clear, h, showError, toast } from "./dom";
import { latestDetail, renderWorkflow } from "./workflows";

function step(done: boolean, title: string, ...body: Array<Node | string | null>): HTMLElement {
  return h("li", { class: done ? "done" : "todo" }, h("strong", {}, done ? t("overview.stepDone", { title }) : title), ...body);
}

function tokenSteps(ctx: Ctx, stack: StackState | null, onSaved: () => void): HTMLElement {
  const p = ctx.project;
  const local = p.mode === "local";
  const msg = h("div", {});
  const open = (path: string) => cmd("open_external", { url: p.url.replace(/\/$/, "") + path }).catch((e) => showError(msg, e));
  const token = h("input", { type: "password", autocomplete: "off", placeholder: t("overview.pastePlaceholder") });
  const check = h("button", { onclick: async () => {
    clear(msg);
    if (!token.value.trim()) return showError(msg, t("overview.pasteFirst"));
    check.disabled = true;
    try {
      const r = await saveToken(p.id, token.value.trim());
      if (r.ok) { token.value = ""; toast(r.message); onSaved(); return; }
      showError(msg, r.message);
    } catch (e) { showError(msg, e); }
    check.disabled = false;
  } }, t("overview.saveCheck"));
  const copy = h("button", { class: "secondary", onclick: async () => {
    clear(msg);
    try {
      await navigator.clipboard.writeText(await cmd<string>("admin_password", { projectId: p.id }));
      toast(t("overview.passwordCopied"));
    } catch (e) { showError(msg, e); }
  } }, t("overview.copyPassword"));
  const running = stack?.state === "running";
  return h("div", { class: "notice" }, h("strong", {}, t("overview.noToken")), h("ol", { class: "steps" },
    ...(local ? [
      step(running, t("overview.stepStart"), h("span", { class: "muted" }, running ? t("overview.stackRuns") : t("overview.stackState", { state: stackLabel(stack?.state ?? "unknown") }))),
      step(false, t("overview.stepPassword"), h("div", { class: "muted" }, t("overview.passwordFile")), copy),
      step(false, t("overview.stepSignIn"), h("div", {}, h("button", { class: "secondary", onclick: () => open("/console/login") }, t("overview.openLogin")))),
    ] : []),
    step(false, t("overview.stepCreate"), h("div", {}, h("button", { class: "secondary", onclick: () => open("/console/tokens") }, t("overview.openTokens")))),
    step(false, t("overview.stepPaste"), h("div", { class: "row" }, token, check))), msg);
}

export async function renderOverview(root: HTMLElement, ctx: Ctx, stack: StackState | null): Promise<() => void> {
  const p = ctx.project;
  const top = h("p", {}, t(stack?.ready ? "overview.systemReady" : "overview.system", { state: stackLabel(stack ? stack.state : "unknown") }));
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
      clear(body, h("h3", {}, t("overview.findings", { n: totalFindings(counts) })),
        totalFindings(counts) ? h("ul", {}, ...Object.entries(counts).map(([k, v]) => h("li", {}, `${k}: ${v}`))) : h("p", { class: "muted" }, t("overview.noFindings")),
        h("h3", {}, t("overview.latest")), wf ? renderWorkflow(wf) : h("p", { class: "muted" }, t("overview.none")));
      return !!wf && workflowActive(wf.status);
    } catch (e) { clear(body); showError(body, e); return false; }
  }
  return autoRefresh(load);
}
