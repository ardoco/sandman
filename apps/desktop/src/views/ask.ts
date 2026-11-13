import { t } from "../i18n";
import { api, ApiError, Ctx } from "../api";
import { ASK_EXAMPLES, ASK_STRATEGIES, AskForm, asOfSummary, askErrorMessage, buildAskBody, pushHistory } from "../logic";
import { clear, field, fmt, h, select, table, toast } from "./dom";

const histories = new Map<string, string[]>();

const block = (summary: string, body: Node[]): HTMLElement | null =>
  body.length ? h("details", {}, h("summary", {}, summary), ...body) : null;

function result(r: any): Node[] {
  const rows: unknown[][] = r.rows ?? [];
  const cls = r.classification;
  const cypher = (r.cypher ?? "") as string;
  return [
    r.answer ? h("p", { class: "answer" }, r.answer) : null,
    r.strategy ? h("p", { class: "muted" }, t("ask.strategyUsed", { strategy: r.strategy }) +
      (cls ? " · " + t("ask.classification", { strategy: cls.strategy, confidence: Number(cls.confidence).toFixed(2) }) : "")) : null,
    r.asOf ? h("p", { class: "muted" }, asOfSummary(r.asOf)) : null,
    r.columns?.length && rows.length ? table(r.columns, rows.map((row) => row.map(fmt))) : r.rows ? h("p", { class: "muted" }, t("ask.noRows")) : null,
    r.truncated ? h("p", { class: "muted" }, t("ask.truncated")) : null,
    cypher ? h("div", {}, h("pre", {}, cypher), h("button", { class: "secondary", onclick: () => {
      void navigator.clipboard.writeText(cypher).then(() => toast(t("ask.copied")));
    } }, t("ask.copy"))) : null,
    block(t("ask.evidence", { count: (r.evidence ?? []).length }), (r.evidence ?? []).map((e: any) =>
      h("p", {}, e.key + (e.score !== undefined ? ` (${Number(e.score).toFixed(2)})` : "") + (e.path?.length ? ` ${e.path.join(" > ")}` : "")))),
    block(t("ask.steps", { count: (r.steps ?? []).length }), (r.steps ?? []).map((s: any) =>
      h("pre", {}, `${s.tool} ${s.input ? JSON.stringify(s.input) : ""}${s.error ? `\n${s.error}` : ""}`))),
    block(t("ask.attempts", { count: (r.attempts ?? []).length }), (r.attempts ?? []).map((a: any) =>
      h("pre", {}, a.cypher + (a.error ? `\n-- ${a.error}` : "")))),
    ...(r.warnings ?? []).map((w: string) => h("p", { class: "muted" }, w)),
  ].filter((n): n is Node => !!n);
}

export function renderAsk(root: HTMLElement, ctx: Ctx): void {
  const pid = ctx.project.id;
  const q = h("input", { type: "text", class: "wide", placeholder: t("ask.placeholder") });
  const strategy = select(ASK_STRATEGIES.map((s): [string, string] => [s, s === "auto" ? t("ask.strategyAuto") : s]), "auto");
  const cypherOnly = h("input", { type: "checkbox" });
  const when = select([["none", t("ask.earlierNone")], ["time", t("ask.earlierTime")], ["revision", t("ask.earlierRevision")]], "none");
  const time = h("input", { type: "datetime-local" });
  const revision = h("input", { type: "text", placeholder: t("ask.revisionPlaceholder") });
  const timeField = field(t("ask.earlierTime"), time);
  const revisionField = field(t("ask.earlierRevision"), revision);
  const out = h("div", {});
  const hist = h("div", { class: "chips" });

  const sync = (): void => {
    timeField.hidden = when.value !== "time";
    revisionField.hidden = when.value !== "revision";
  };
  const drawHistory = (): void => {
    const list = histories.get(pid) ?? [];
    clear(hist, ...(list.length ? [h("small", {}, t("ask.history")), ...list.map((x) =>
      h("button", { class: "secondary chip", onclick: () => { q.value = x; q.focus(); } }, x))] : []));
  };
  const go = async (): Promise<void> => {
    clear(out);
    if (q.value.trim().length < 3) { clear(out, h("p", { class: "error" }, t("ask.tooShort"))); return; }
    const form: AskForm = { question: q.value, strategy: strategy.value, cypherOnly: cypherOnly.checked, when: when.value as AskForm["when"], time: time.value, revision: revision.value };
    clear(out, h("p", { class: "muted" }, t("ask.working")));
    try {
      const r = await api(pid, "POST", "/v1/query/nl", undefined, buildAskBody(pid, form));
      histories.set(pid, pushHistory(histories.get(pid) ?? [], q.value));
      drawHistory();
      clear(out, ...result(r));
    } catch (e) {
      const msg = e instanceof ApiError ? askErrorMessage(e.status, e.message) : e instanceof Error ? e.message : String(e);
      clear(out, h("p", { class: "error" }, msg));
    }
  };

  const chips = ASK_EXAMPLES.map((ex) => h("button", { class: "secondary chip", onclick: () => {
    q.value = ex.text;
    q.focus();
    q.setSelectionRange(ex.text.length, ex.text.length);
  } }, ex.label));
  when.addEventListener("change", sync);
  q.addEventListener("keydown", (e) => e.key === "Enter" && void go());
  sync();
  drawHistory();
  clear(root,
    h("div", { class: "row" }, q, h("button", { onclick: go }, t("ask.button"))),
    h("div", { class: "chips" }, h("small", {}, t("ask.examples")), ...chips),
    h("div", { class: "row" },
      field(t("ask.strategy"), strategy),
      h("label", { class: "check" }, cypherOnly, h("span", {}, t("ask.cypherOnly"))),
      field(t("ask.earlier"), when), timeField, revisionField),
    hist, out);
}
