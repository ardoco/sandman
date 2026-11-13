import { formatNumber, t } from "../i18n";
import { cmd, Ctx } from "../api";
import { CLASS_KINDS, classView, ClassFilter, ClassScan, ClassView, editList, initialOpen, setEdit, unreviewedMessage } from "../logic";
import { clear, h, select, showError, toast } from "./dom";

export const shared: { dir: string } = { dir: "" };

interface Preview { block: string; diff: string; written: boolean; warnings: string[]; }

export async function renderClassification(root: HTMLElement, ctx: Ctx): Promise<() => void> {
  const id = ctx.project.id;
  let scan: ClassScan | undefined;
  let edits = new Map<string, string>();
  let open = new Set<string>();
  let filter: ClassFilter = "all";
  let preview: Preview | undefined;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const msg = h("div", {});
  const head = h("div", { class: "muted" });
  const tree = h("div", { class: "ctree", role: "tree" });
  const block = h("div", {});
  const search = h("input", { type: "search", placeholder: t("class.search") });
  const folder = h("div", { class: "muted" });

  const choose = async () => { const p = await cmd<string | null>("pick_folder"); if (p) { shared.dir = p; await load(); } };
  async function load(): Promise<void> {
    clear(msg);
    clear(folder, shared.dir || t("class.noFolder"));
    if (!shared.dir) return;
    try {
      scan = JSON.parse(await cmd<string>("classify_scan", { projectId: id, dir: shared.dir }));
      edits = new Map();
      open = initialOpen(scan!.rows);
      preview = undefined;
      draw();
    } catch (e) { scan = undefined; clear(tree); showError(msg, e); }
  }

  function kindMenu(r: ClassView): HTMLSelectElement {
    const m = select([...CLASS_KINDS, ...(r.effKind === "mixed" ? ["mixed"] : [])], r.effKind);
    m.value = r.effKind;
    m.setAttribute("aria-label", t("class.kindOf", { path: r.path }));
    m.addEventListener("change", () => {
      edits = setEdit(edits, scan!.rows, r.path, m.value);
      draw();
      schedule();
    });
    return m;
  }
  function rowEl(r: ClassView): HTMLElement {
    const toggle = r.type === "dir"
      ? h("button", { class: "secondary", "aria-expanded": r.isOpen, "aria-label": t(r.isOpen ? "class.closeRow" : "class.openRow", { path: r.path }), onclick: () => { r.isOpen ? open.delete(r.path) : open.add(r.path); draw(); } }, r.isOpen ? "v" : ">")
      : h("span", { class: "spacer" });
    const who = r.edited ? t("class.you") : r.source ?? "";
    return h("div", { class: `crow${r.edited ? " edited" : ""}`, role: "treeitem", tabindex: "0", "data-path": r.path, title: r.reason ? `${r.source}: ${r.reason}` : "", style: `padding-left:${r.depth * 20}px` },
      h("span", { class: "weak" }, r.stillWeak ? "?" : ""), toggle, kindMenu(r),
      h("span", { class: "cpath" }, r.type === "dir" ? `${r.name}/` : r.name), h("span", { class: "muted" }, who),
      h("span", { class: "muted" }, r.type === "dir" ? t(r.files === 1 ? "class.filesOne" : "class.filesMany", { n: formatNumber(r.files) }) : ""));
  }
  function draw(): void {
    if (!scan) return;
    const left = scan.files.filter((f) => f.weak && ![...edits.keys()].some((e) => f.path === e || f.path.startsWith(`${e}/`)));
    clear(head, t("class.head", {
      count: formatNumber(scan.fileCount),
      version: scan.classifierVersion,
      review: scan.review.weak === 0 ? t("class.reviewNothing") : left.length === 0 ? t("class.reviewHandled") : scan.review.reviewed ? t("class.reviewDone") : unreviewedMessage(left),
    }),
      ...scan.warnings.map((w) => h("div", { class: "error" }, w)));
    const view = classView(scan.rows, edits, open, filter, search.value);
    clear(tree, ...view.map(rowEl), view.length ? null : h("span", { class: "muted" }, t("class.noRows")));
    drawBlock();
  }
  function drawBlock(): void {
    const n = edits.size;
    clear(block,
      h("h3", {}, t("class.blockTitle")),
      h("p", { class: "caution" }, t("class.caution")),
      preview ? h("pre", {}, preview.block) : h("span", { class: "muted" }, n ? t("class.loadingPreview") : t("class.noChanges")),
      preview?.diff ? h("details", {}, h("summary", {}, t("class.viewDiff")), h("pre", {}, preview.diff)) : null,
      ...(preview?.warnings ?? []).map((w) => h("div", { class: "error" }, w)),
      h("div", { class: "row" },
        h("button", { class: "secondary", disabled: !n, onclick: () => { edits = new Map(); preview = undefined; draw(); } }, t("class.reset")),
        h("button", { disabled: !n, onclick: () => void save(false) }, t("common.save")),
        h("button", { disabled: !n, onclick: () => void save(true) }, t("class.saveGo"))));
  }
  async function call(write: boolean): Promise<Preview> {
    return JSON.parse(await cmd<string>("classify_rules", { projectId: id, dir: shared.dir, edits: editList(edits), write }));
  }
  function schedule(): void {
    clearTimeout(timer);
    if (!edits.size) { preview = undefined; drawBlock(); return; }
    drawBlock();
    timer = setTimeout(async () => {
      try { preview = await call(false); drawBlock(); } catch (e) { clear(msg); showError(msg, e); }
    }, 250);
  }
  async function save(next: boolean): Promise<void> {
    clear(msg);
    try {
      const r = await call(true);
      toast(t(r.written ? "class.savedYaml" : "class.nothingToWrite"));
      await load();
      if (next) ctx.go("Ingest");
    } catch (e) { showError(msg, e); }
  }

  search.addEventListener("input", draw);
  tree.addEventListener("keydown", (ev) => {
    const el = (ev.target as HTMLElement).closest<HTMLElement>(".crow");
    if (!el || (ev.target as HTMLElement).tagName === "SELECT") return;
    const path = el.dataset.path!;
    const rows = [...tree.querySelectorAll<HTMLElement>(".crow")];
    const i = rows.indexOf(el);
    const move = (n: number) => { ev.preventDefault(); rows[i + n]?.focus(); };
    const setOpen = (v: boolean) => { ev.preventDefault(); v ? open.add(path) : open.delete(path); draw(); tree.querySelector<HTMLElement>(`[data-path="${CSS.escape(path)}"]`)?.focus(); };
    if (ev.key === "ArrowDown") move(1);
    else if (ev.key === "ArrowUp") move(-1);
    else if (ev.key === "ArrowRight") setOpen(true);
    else if (ev.key === "ArrowLeft") setOpen(false);
    else if (ev.key === "Enter") { ev.preventDefault(); el.querySelector("select")?.focus(); }
  });
  const onSlash = (ev: KeyboardEvent) => {
    if (ev.key === "/" && !(ev.target instanceof HTMLInputElement) && !(ev.target instanceof HTMLSelectElement)) { ev.preventDefault(); search.focus(); }
  };
  document.addEventListener("keydown", onSlash);

  const radio = (value: ClassFilter, label: string) =>
    h("label", {}, h("input", { type: "radio", name: "cfilter", value, checked: value === filter, onchange: () => { filter = value; draw(); } }), ` ${label} `);
  clear(root,
    h("div", { class: "row" }, h("button", { class: "secondary", onclick: () => void choose() }, t("common.pickFolder")), h("button", { class: "secondary", onclick: () => void load() }, t("class.rescan"))),
    folder, head,
    h("div", { class: "row" }, t("class.show") + " ", radio("all", t("class.filterAll")), radio("weak", t("class.filterWeak")), radio("changed", t("class.filterChanged")), search),
    msg, tree, h("div", { class: "muted" }, t("class.legend")), block);
  await load();
  return () => { clearTimeout(timer); document.removeEventListener("keydown", onSlash); };
}
