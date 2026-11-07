import { api, Ctx } from "../api";
import { clear, fmt, h, showError, table } from "./dom";

export function renderAsk(root: HTMLElement, ctx: Ctx): void {
  const q = h("input", { type: "text", placeholder: "Ask a question about the project" });
  const out = h("div", {});
  const go = async (): Promise<void> => {
    if (q.value.trim().length < 3) return;
    clear(out, h("p", { class: "muted" }, "Working..."));
    try {
      const r = await api(ctx.project.id, "POST", "/v1/query/nl", undefined, { projectId: ctx.project.id, question: q.value });
      clear(out,
        r.answer ? h("p", {}, r.answer) : null,
        r.rows?.length ? table(r.columns, r.rows.map((row: unknown[]) => row.map(fmt))) : null,
        r.cypher ? h("pre", {}, r.cypher) : null,
        ...(r.warnings ?? []).map((w: string) => h("p", { class: "muted" }, w)));
    } catch (e) { clear(out); showError(out, e); }
  };
  clear(root, h("div", { class: "row" }, q, h("button", { onclick: go }, "Ask")), out);
  q.addEventListener("keydown", (e) => e.key === "Enter" && go());
}
