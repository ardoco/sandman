import { listen } from "@tauri-apps/api/event";
import { cmd, Ctx, Lifecycle, Project, StackState } from "../api";
import { lastLines, windowTitle } from "../logic";
import { clear, h, toast } from "./dom";
import { renderAsk } from "./ask";
import { renderFindings } from "./findings";
import { renderGraph } from "./graph";
import { renderIngest } from "./ingest";
import { renderLinks } from "./links";
import { renderOverview } from "./overview";
import { renderTraceView } from "./traceview";
import { renderSettings } from "./settings";
import { renderWorkflows } from "./workflows";

const TABS = ["Overview", "Ingest", "Workflows", "Findings", "Trace links", "Trace viewer", "Ask", "Graph", "Settings"];

export async function renderProject(root: HTMLElement, project: Project, leave: () => void): Promise<void> {
  const local = project.mode === "local";
  let stack: StackState | null = null;
  let cleanup: (() => void) | void = undefined;
  let tab = "Overview";
  const state = h("span", { class: "muted" });
  const nav = h("nav", {});
  const content = h("div", { class: "content" });
  const log = h("pre", { class: "log", hidden: true });
  const start = h("button", { onclick: () => run("stack_up") }, "Start");
  const stop = h("button", { class: "secondary", onclick: () => run("stack_down") }, "Stop");
  start.hidden = stop.hidden = !local;
  const ctx: Ctx = { project, go: (t) => select(t) };

  async function run(command: "stack_up" | "stack_down"): Promise<void> {
    log.hidden = false;
    clear(log);
    start.disabled = stop.disabled = true;
    try {
      const r = await cmd<Lifecycle>(command, { projectId: project.id });
      if (r.code === 0) toast("Done.");
      else {
        const tail = lastLines(log.textContent?.split("\n") ?? []);
        toast(`${r.hint ?? `Command failed (exit ${r.code}).`}${tail ? `\n${tail}` : ""}`, "error");
      }
    } catch (e) { toast(String(e instanceof Error ? e.message : e), "error"); }
    start.disabled = stop.disabled = false;
    await refresh();
    select(tab);
  }
  async function refresh(): Promise<void> {
    if (!local) return;
    try { stack = await cmd<StackState>("stack_state", { projectId: project.id }); } catch { stack = null; }
    clear(state, stack ? `${stack.state}${stack.ready ? " (ready)" : ""}` : "unknown");
    void cmd("set_title", { title: windowTitle(project.name, local ? (stack?.state ?? "unknown") : null) }).catch(() => undefined);
  }
  async function select(t: string): Promise<void> {
    tab = t;
    cleanup?.();
    cleanup = undefined;
    nav.querySelectorAll("button").forEach((b) => b.classList.toggle("active", b.textContent === t));
    const view = h("div", {});
    clear(content, view);
    try {
      switch (t) {
        case "Overview": cleanup = await renderOverview(view, ctx, stack); break;
        case "Ingest": cleanup = await renderIngest(view, ctx); break;
        case "Workflows": cleanup = await renderWorkflows(view, ctx); break;
        case "Findings": await renderFindings(view, ctx); break;
        case "Trace links": await renderLinks(view, ctx); break;
        case "Trace viewer": cleanup = await renderTraceView(view, ctx); break;
        case "Ask": renderAsk(view, ctx); break;
        case "Graph": cleanup = await renderGraph(view, ctx); break;
        default: await renderSettings(view, ctx);
      }
    } catch (e) { view.append(h("p", { class: "error" }, e instanceof Error ? e.message : String(e))); }
  }
  async function confirmClose(): Promise<boolean> {
    if (!local) return true;
    await refresh();
    if (!stack || stack.state === "stopped") return true;
    return new Promise((resolve) => {
      const done = (v: boolean) => { dlg.remove(); resolve(v); };
      const command = `make -C ${project.checkout} down`;
      const dlg = h("div", { class: "modal" }, h("div", { class: "dialog" },
        h("p", {}, "The local stack is still running."),
        h("pre", {}, command),
        h("div", { class: "row" },
          h("button", { onclick: async () => { await cmd("stack_down", { projectId: project.id }).catch(() => undefined); done(true); } }, "Stop containers"),
          h("button", { class: "secondary", onclick: () => { navigator.clipboard?.writeText(command); done(true); } }, "Keep running (copy command)"),
          h("button", { class: "secondary", onclick: () => done(false) }, "Cancel"))));
      document.body.append(dlg);
    });
  }

  const unStack = await listen<string>("stack-log", (e) => { log.append(e.payload + "\n"); log.scrollTop = log.scrollHeight; });
  const unClose = await listen("close-requested", async () => { if (await confirmClose()) await cmd("quit_app"); });
  const timer = setInterval(refresh, 5000);
  const onKey = (e: KeyboardEvent) => {
    const n = Number(e.key);
    if ((e.ctrlKey || e.metaKey) && n >= 1 && n <= TABS.length) { e.preventDefault(); void select(TABS[n - 1]); }
  };
  document.addEventListener("keydown", onKey);
  const teardown = () => {
    clearInterval(timer); unStack(); unClose(); cleanup?.();
    document.removeEventListener("keydown", onKey);
    void cmd("set_title", { title: "SANDMAN" }).catch(() => undefined);
  };

  clear(root,
    h("header", {}, h("strong", {}, project.name), state, start, stop,
      h("button", { class: "secondary", onclick: async () => { if (await confirmClose()) { teardown(); leave(); } } }, "Switch project")),
    nav, log, content);
  nav.append(...TABS.map((t) => h("button", { class: "tab", onclick: () => select(t) }, t)));
  if (local) {
    const names = project.cliCommand ? ["make", "docker"] : ["make", "docker", "uv"];
    const missing = await cmd<string[]>("missing_tools", { names }).catch(() => []);
    if (missing.length) content.before(h("p", { class: "notice" }, `Missing program: ${missing.join(", ")}. Install ${missing.length > 1 ? "them" : "it"} and restart the app.`));
  }
  await refresh();
  await select("Overview");
}
