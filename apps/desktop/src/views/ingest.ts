import { cmd, Ctx } from "../api";
import { listen } from "@tauri-apps/api/event";
import { clear, field, h, select, showError } from "./dom";
import { latestDetail, renderWorkflow } from "./workflows";

export async function renderIngest(root: HTMLElement, ctx: Ctx): Promise<() => void> {
  let paths: string[] = [];
  const chosen = h("div", { class: "muted" }, "Nothing picked.");
  const set = (p: string[]) => { paths = p; clear(chosen, p.join("\n")); };
  const mode = select(["full", "partial"], "full");
  const revision = h("input", { type: "text", placeholder: "optional source revision" });
  const log = h("pre", { class: "log" });
  const progress = h("div", {});
  const msg = h("div", {});
  const run = h("button", { onclick: async () => {
    clear(msg);
    if (!paths.length) return showError(msg, "Pick a folder or files first.");
    run.disabled = true;
    clear(log);
    try {
      const code = await cmd<number>("ingest", { projectId: ctx.project.id, paths, mode: mode.value, revision: revision.value || null });
      log.append(`\nexit code ${code}\n`);
    } catch (e) { showError(msg, e); }
    run.disabled = false;
    poll();
  } }, "Ingest");
  clear(root,
    h("div", { class: "row" },
      h("button", { class: "secondary", onclick: async () => { const p = await cmd<string | null>("pick_folder"); if (p) set([p]); } }, "Pick folder"),
      h("button", { class: "secondary", onclick: async () => { const p = await cmd<string[]>("pick_files"); if (p.length) set(p); } }, "Pick files")),
    chosen, field("Mode", mode, "Files are always sent as partial."), field("Revision", revision), run, msg, log, h("h3", {}, "Workflow"), progress);
  const un = await listen<string>("ingest-log", (e) => { log.append(e.payload + "\n"); log.scrollTop = log.scrollHeight; });
  async function poll(): Promise<void> {
    try { const wf = await latestDetail(ctx.project.id); clear(progress, wf ? renderWorkflow(wf) : h("span", { class: "muted" }, "None yet.")); }
    catch (e) { clear(progress); showError(progress, e); }
  }
  await poll();
  const t = setInterval(poll, 2000);
  return () => { clearInterval(t); un(); };
}
