import { api, Ctx, hasToken, StackState } from "../api";
import { totalFindings } from "../logic";
import { clear, h, showError } from "./dom";
import { latestDetail, renderWorkflow } from "./workflows";

export async function renderOverview(root: HTMLElement, ctx: Ctx, stack: StackState | null): Promise<void> {
  const p = ctx.project;
  clear(root, h("p", {}, `System: ${stack ? stack.state : "unknown"}${stack?.ready ? " (ready)" : ""}`));
  if (!(await hasToken(p.id))) {
    root.append(h("div", { class: "notice" }, h("strong", {}, "No token yet."), h("ol", {},
      ...(p.mode === "local" ? [h("li", {}, "Start the stack."), h("li", {}, "Open the console and sign in as admin."), h("li", {}, "Change the admin password."), h("li", {}, "Create a token.")] : [h("li", {}, "Create a token in the console.")]),
      h("li", {}, "Paste the token in Settings."))),
      h("button", { onclick: () => ctx.go("Settings") }, "Go to Settings"));
    return;
  }
  try {
    const f = await api(p.id, "GET", `/v1/projects/${p.id}/inconsistencies`, { limit: "1" });
    const counts: Record<string, number> = f.counts ?? {};
    root.append(h("h3", {}, `Findings (${totalFindings(counts)})`),
      h("ul", {}, ...Object.entries(counts).map(([k, v]) => h("li", {}, `${k}: ${v}`))));
    const wf = await latestDetail(p.id);
    root.append(h("h3", {}, "Latest workflow"), wf ? renderWorkflow(wf) : h("p", { class: "muted" }, "None yet."));
  } catch (e) { showError(root, e); }
}
