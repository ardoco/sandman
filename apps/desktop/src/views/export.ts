import { t } from "../i18n";
import { cmd, Ctx, cypher } from "../api";
import { availableFormats, exportFileName, exportSummary, ExportWhat, GENERIC_FORMATS } from "../logic";
import { clear, field, h, select, showError, toast } from "./dom";

const APPROACHES = "MATCH (l:TraceLink {projectId: $projectId, active: true}) RETURN DISTINCT l.approach AS approach ORDER BY approach";

export async function renderExport(root: HTMLElement, ctx: Ctx): Promise<void> {
  const id = ctx.project.id;
  const approaches = await cypher(id, APPROACHES).then((r) => r.rows.map((row) => String(row[0]))).catch(() => [] as string[]);

  function block(what: ExportWhat): HTMLElement {
    const formats = availableFormats(ctx.project, what);
    const format = select(formats.map((f): [string, string] => [f.format, f.format]), "csv");
    formats.forEach((f, i) => { format.options[i].disabled = f.disabled; });
    const approach = select([["", t("export.allApproaches")], ...approaches.map((a): [string, string] => [a, a])]);
    const min = h("input", { type: "number", min: "0", max: "1", step: "0.05", value: "0" });
    const rejected = h("input", { type: "checkbox" });
    const codeRoot = h("input", { type: "text" });
    const fApproach = field(t("common.approach"), approach);
    const fMin = field(t("export.minConfidence"), min);
    const fRejected = field(t("export.includeRejected"), rejected);
    const fCode = field(t("export.codeRoot"), codeRoot, t("export.codeRootHint"));
    const out = h("div", {});
    const button = h("button", { onclick: () => save() }, t("export.button"));
    const generic = () => GENERIC_FORMATS.includes(format.value);
    const sync = () => {
      fApproach.hidden = what !== "tracelinks";
      fMin.hidden = fRejected.hidden = what !== "tracelinks" || !generic();
      fCode.hidden = format.value !== "ardoco-sad-code";
    };
    format.addEventListener("change", sync);
    sync();

    async function save(): Promise<void> {
      clear(out);
      const choice = what === "tracelinks" ? approach.value : "";
      const path = await cmd<string | null>("pick_save", { fileName: exportFileName(ctx.project.id, what, format.value, choice) }).catch((e) => { showError(out, e); return null; });
      if (!path) return;
      button.disabled = true;
      clear(out, h("p", { class: "muted" }, t("export.working")));
      try {
        const common = { projectId: id, format: format.value, approach: choice || undefined, path };
        const r = await cmd<{ rows: number; path: string }>(
          generic() ? "export_generic" : "export_benchmark",
          generic()
            ? { ...common, what, minConfidence: what === "tracelinks" ? Number(min.value) || 0 : undefined, includeRejected: what === "tracelinks" ? rejected.checked : undefined }
            : { ...common, codeRoot: format.value === "ardoco-sad-code" ? codeRoot.value.trim() || undefined : undefined });
        const text = exportSummary(r.rows, r.path);
        toast(text);
        clear(out, h("p", {}, text), h("button", { class: "secondary", onclick: () => void cmd("reveal_file", { path: r.path }).catch((e) => showError(out, e)) }, t("export.reveal")));
      } catch (e) { clear(out); showError(out, e); }
      button.disabled = false;
    }

    return h("section", {}, h("h3", {}, t(what === "tracelinks" ? "tab.traceLinks" : "tab.findings")),
      h("div", { class: "row" }, field(t("export.format"), format), fApproach, fMin, fRejected, fCode, button),
      formats.some((f) => f.disabled) ? h("p", { class: "muted" }, t("export.noCli")) : null, out);
  }

  clear(root, block("tracelinks"), block("findings"));
}
