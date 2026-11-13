import { t } from "../i18n";
import { api, cmd, Ctx } from "../api";
import { listen } from "@tauri-apps/api/event";
import { classSummary, ClassScan, lastLines, needsReview, unreviewedMessage, workflowActive } from "../logic";
import { autoRefresh, clear, field, h, select, showError } from "./dom";
import { shared } from "./classification";
import { renderWorkflow } from "./workflows";

const CANCELLED = 130;

export async function renderIngest(root: HTMLElement, ctx: Ctx): Promise<() => void> {
  const id = ctx.project.id;
  let paths: string[] = [];
  let what = "folder";
  let followed: string | undefined;
  let stopFollow: (() => void) | undefined;
  const chosen = h("div", { class: "muted" }, t("ingest.nothingPicked"));
  const preview = h("pre", {});
  const mode = select(["full", "partial"], "full");
  const revision = h("input", { type: "text", placeholder: t("ingest.revisionPlaceholder") });
  const log = h("pre", { class: "log" });
  const progress = h("div", {});
  const msg = h("div", {});
  const summary = h("div", { class: "muted" });
  const cli = () => ctx.project.cliCommand?.trim() || "uv run --project <checkout> sandman";
  const refreshPreview = () => {
    const m = paths.length === 1 && what === "folder" ? mode.value : paths.length ? "partial" : mode.value;
    clear(preview, `${cli()} ingest --project ${id} --mode ${m}${revision.value ? ` --revision=${revision.value}` : ""} -- ${paths.length === 1 ? paths[0] : "<staged files>"}`);
  };
  const set = (p: string[], kind: string) => {
    paths = p;
    what = kind;
    clear(chosen, `${kind === "folder" ? t("ingest.pickedFolder") : t("ingest.pickedFiles", { count: p.length })}\n${p.join("\n")}`);
    refreshPreview();
    void showSummary();
  };
  async function scanPicked(): Promise<ClassScan | undefined> {
    if (paths.length !== 1 || what !== "folder") return undefined;
    shared.dir = paths[0];
    return JSON.parse(await cmd<string>("classify_scan", { projectId: id, dir: paths[0] }));
  }
  async function showSummary(): Promise<void> {
    clear(summary);
    try {
      const s = await scanPicked();
      if (s) clear(summary, t("ingest.classification", { summary: classSummary(s) }) + " ", h("a", { href: "#", onclick: (e: Event) => { e.preventDefault(); ctx.go("Classification"); } }, t("ingest.openClassification")));
    } catch (e) { clear(summary, t("ingest.classUnavailable", { error: e instanceof Error ? e.message : String(e) })); }
  }
  function askReview(text: string): Promise<"review" | "anyway" | "cancel"> {
    return new Promise((resolve) => {
      const done = (v: "review" | "anyway" | "cancel") => { dlg.remove(); resolve(v); };
      const dlg = h("div", { class: "modal" }, h("div", { class: "dialog", role: "dialog" },
        h("p", {}, text),
        h("div", { class: "row" },
          h("button", { onclick: () => done("review") }, t("ingest.reviewNow")),
          h("button", { class: "secondary", onclick: () => done("anyway") }, t("ingest.anyway")),
          h("button", { class: "secondary", onclick: () => done("cancel") }, t("common.cancel")))));
      document.body.append(dlg);
    });
  }
  mode.addEventListener("change", refreshPreview);
  revision.addEventListener("input", refreshPreview);

  const cancel = h("button", { class: "secondary", hidden: true, onclick: () => void cmd("ingest_cancel", { projectId: id }) }, t("common.cancel"));
  const run = h("button", { onclick: async () => {
    clear(msg);
    if (!paths.length) return showError(msg, t("ingest.pickFirst"));
    let noReview = false;
    try {
      const s = await scanPicked();
      if (s && needsReview(s)) {
        const choice = await askReview(unreviewedMessage(s.files));
        if (choice === "review") return ctx.go("Classification");
        if (choice === "cancel") return;
        noReview = true;
      }
    } catch (e) { return showError(msg, e); }
    run.disabled = true;
    cancel.hidden = false;
    clear(log);
    stopFollow?.();
    try {
      const before = await latestId();
      const code = await cmd<number>("ingest", { projectId: id, paths, mode: mode.value, revision: revision.value || null, noReview });
      if (code === CANCELLED) log.append(`\n${t("ingest.cancelled")}\n`);
      else if (code !== 0) {
        log.append(`\n${t("ingest.exitCode", { code })}\n`);
        showError(msg, t("ingest.cliFailed", { code, lines: lastLines((log.textContent ?? "").split("\n").filter(Boolean)) }));
      } else {
        log.append(`\n${t("common.done")}\n`);
        follow(before);
      }
    } catch (e) { showError(msg, e); }
    run.disabled = false;
    cancel.hidden = true;
  } }, t("ingest.run"));

  async function latestId(): Promise<string | undefined> {
    const list = await api(id, "GET", `/v1/projects/${id}/workflows`, { limit: "20" }).catch(() => ({ items: [] }));
    return [...list.items].sort((a: any, b: any) => String(b.createdAt).localeCompare(String(a.createdAt)))[0]?.id;
  }
  function follow(before: string | undefined): void {
    let tries = 0;
    stopFollow = autoRefresh(async () => {
      if (!followed) {
        const now = await latestId();
        if (now && now !== before) followed = now;
        else return ++tries < 15;
      }
      const wf = await api(id, "GET", `/v1/workflows/${followed}`);
      clear(progress, renderWorkflow(wf));
      return workflowActive(wf.status);
    }, 2000);
  }

  clear(root,
    h("div", { class: "row" },
      h("button", { class: "secondary", onclick: async () => { const p = await cmd<string | null>("pick_folder"); if (p) set([p], "folder"); } }, t("common.pickFolder")),
      h("button", { class: "secondary", onclick: async () => { const p = await cmd<string[]>("pick_files"); if (p.length) set(p, "files"); } }, t("ingest.pickFiles"))),
    chosen, summary, field(t("ingest.mode"), mode, t("ingest.modeHint")), field(t("ingest.revision"), revision),
    h("div", { class: "muted" }, t("ingest.command")), preview,
    h("div", { class: "row" }, run, cancel), msg, log, h("h3", {}, t("ingest.workflow")), progress);
  refreshPreview();
  const un = await listen<string>("ingest-log", (e) => { log.append(e.payload + "\n"); log.scrollTop = log.scrollHeight; });
  clear(progress, h("span", { class: "muted" }, t("ingest.startsAfter")));
  return () => { stopFollow?.(); un(); };
}
