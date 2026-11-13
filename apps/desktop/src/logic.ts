import { Key, formatNumber, localizeError, setLanguage, t } from "./i18n";

export const SLUG = /^[a-z0-9][a-z0-9-]{1,62}$/;
export const WORKFLOW_DONE = ["SUCCEEDED", "FAILED", "CANCELED", "BLOCKED"];

export const validSlug = (s: string): boolean => SLUG.test(s);

export function slugify(name: string): string {
  return name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 63).replace(/-+$/, "");
}

export function problemMessage(status: number, body: unknown): string {
  if (body && typeof body === "object") {
    const p = body as { detail?: unknown; title?: unknown; errors?: unknown };
    if (typeof p.detail === "string" && p.detail) return p.detail;
    if (Array.isArray(p.errors) && p.errors.length) return p.errors.join("; ");
    if (typeof p.title === "string" && p.title) return p.title;
  }
  if (typeof body === "string" && body) return body.slice(0, 300);
  return `HTTP ${status}`;
}

export interface Step {
  skill: string;
  agent: string;
  status: string;
  attempts: number;
  activity?: { text?: string };
}
export interface WorkflowLike {
  status: string;
  steps?: Step[];
}

export function workflowProgress(wf: WorkflowLike): { done: number; total: number; percent: number; label: string; finished: boolean } {
  const steps = wf.steps ?? [];
  const done = steps.filter((s) => s.status === "SUCCEEDED").length;
  const running = steps.find((s) => s.status === "RUNNING");
  const finished = WORKFLOW_DONE.includes(wf.status);
  const percent = steps.length ? Math.round((done / steps.length) * 100) : finished ? 100 : 0;
  const label = running
    ? `${running.skill} (${running.agent})${running.activity?.text ? ": " + running.activity.text : ""}`
    : wf.status;
  return { done, total: steps.length, percent, label, finished };
}

export function latestWorkflow<T extends { createdAt: string }>(items: T[]): T | undefined {
  return [...items].sort((a, b) => b.createdAt.localeCompare(a.createdAt))[0];
}

export function totalFindings(counts: Record<string, number>): number {
  return Object.values(counts).reduce((a, b) => a + b, 0);
}

export function needsCause(to: string): boolean {
  return ["ACCEPTED", "REJECTED", "DEFERRED"].includes(to);
}

export function dashboardUrl(url: string): string {
  const u = new URL(url);
  u.port = "8090";
  return u.origin;
}

export const reviewable = (approach: string): boolean => !["explicit-reference", "user-declared"].includes(approach);

export const workflowActive = (status: string): boolean => status === "RUNNING" || status === "PENDING";

export function tokenMessage(status: number, body: unknown): string {
  if (status === 200) return t("msg.tokenAccepted");
  if (status === 401) return t("msg.tokenRejected");
  if (status === 403) {
    const detail = problemMessage(status, body);
    return /password change/i.test(detail)
      ? t("msg.passwordChangeToken")
      : detail;
  }
  return problemMessage(status, body);
}

export type LoginMode = "password" | "token";

export const loginFields = (mode: LoginMode): string[] => (mode === "password" ? ["URL", "Username", "Password"] : ["URL", "Token"]);

export function loginMessage(status: number, body: unknown): string {
  if (status === 401) return t("msg.loginWrong");
  if (status === 429) return t("msg.loginTooMany");
  if (status === 403 && /password change/i.test(problemMessage(status, body))) {
    return t("msg.passwordChangeLogin");
  }
  return problemMessage(status, body);
}

export interface ConnectResult {
  username: string;
  added: string[];
  updated: string[];
  skipped: { id: string; reason: string }[];
  warning: string | null;
}

export function connectSummary(r: ConnectResult): string[] {
  const lines = [t("connect.connectedAs", { name: r.username })];
  if (r.added.length) lines.push(t("connect.added", { list: r.added.join(", ") }));
  if (r.updated.length) lines.push(t("connect.updated", { list: r.updated.join(", ") }));
  for (const s of r.skipped) lines.push(t("connect.skipped", { id: s.id, reason: s.reason }));
  if (!r.added.length && !r.updated.length && !r.skipped.length) lines.push(t("connect.noProject"));
  if (r.warning) lines.push(localizeError(r.warning));
  return lines;
}

export const prefillUrl = (projects: { mode: string; url: string }[]): string => projects.find((p) => p.mode === "remote")?.url ?? "";

export const localProject = <T extends { mode: string }>(projects: T[]): T | undefined => projects.find((p) => p.mode === "local");

export type Language = "en" | "de";

export const resolveLanguage = (setting: string, navigatorLanguage: string): Language =>
  setting === "de" || (setting !== "en" && navigatorLanguage.toLowerCase().startsWith("de")) ? "de" : "en";

export interface AppSettingsLike { language: string; theme: string; fontSize: string; }
export const DEFAULT_SETTINGS: AppSettingsLike = { language: "system", theme: "system", fontSize: "normal" };

export function applySettings(s: AppSettingsLike, root: HTMLElement = document.documentElement, navigatorLanguage: string = navigator.language): void {
  root.dataset.theme = s.theme;
  root.dataset.font = s.fontSize;
  root.lang = resolveLanguage(s.language, navigatorLanguage);
  setLanguage(root.lang as Language);
}

export type SettingsTab = "general" | "project";

export const settingsTabState = (hasProject: boolean): { first: SettingsTab; projectDisabled: boolean } =>
  ({ first: hasProject ? "project" : "general", projectDisabled: !hasProject });

const STACK_STATES = ["stopped", "starting", "running", "partial", "remote", "unknown"];
export const stackLabel = (state: string): string => (STACK_STATES.includes(state) ? t(`stack.${state}` as Key) : state);

export function windowTitle(name: string, stack: string | null): string {
  return stack ? `${name} - ${stackLabel(stack)} - SANDMAN` : `${name} - SANDMAN`;
}

export function lastLines(lines: string[], n = 15): string {
  return lines.slice(-n).join("\n");
}

export interface TvElement { id: string; label: string; parentId?: string | null; artifactId?: string; }
export interface TvLink { sourceId: string; targetId: string; approach?: string | null; confidence?: number | null; status: string; }
export interface TvFinding { severity: string; status: string; involved: { id: string; role?: string }[]; }

export const COLUMNS = ["Requirements", "Documentation", "Architecture", "Code", "Tests"];
export const columnLabel = (i: number): string => t(`column.${COLUMNS[i].toLowerCase()}` as Key);
const COLUMN_OF: Record<string, number> = { Requirement: 0, TextUnit: 1, Decision: 1, ModelElement: 2, CodeUnit: 3, TestCase: 4 };
export const columnOf = (label: string): number => COLUMN_OF[label] ?? -1;

export function columnsLayout(
  elements: TvElement[],
  nodeW = 240, gapX = 120, rowH = 64, indent = 24,
): Map<string, { x: number; y: number; column: number; depth: number }> {
  const byId = new Map(elements.map((e) => [e.id, e]));
  const depthOf = (e: TvElement): number => {
    let d = 0;
    for (let p = e.parentId ? byId.get(e.parentId) : undefined; p && d < 20; p = p.parentId ? byId.get(p.parentId) : undefined) d++;
    return d;
  };
  const next: number[] = COLUMNS.map(() => 0);
  const out = new Map<string, { x: number; y: number; column: number; depth: number }>();
  for (const e of elements) {
    const column = columnOf(e.label);
    if (column < 0) continue;
    const depth = depthOf(e);
    out.set(e.id, { x: column * (nodeW + gapX) + depth * indent, y: next[column]++ * rowH, column, depth });
  }
  return out;
}

export interface LinkFilter { statuses: Set<string>; approaches: Set<string>; minConfidence: number; }

export function filterLinks<T extends TvLink>(links: T[], f: LinkFilter): T[] {
  return links.filter((l) =>
    f.statuses.has(l.status) &&
    (f.approaches.size === 0 || f.approaches.has(l.approach ?? "")) &&
    (l.confidence ?? 0) >= f.minConfidence);
}

const SEVERITY_RANK: Record<string, number> = { info: 1, warning: 2, error: 3 };
export const findingOpen = (status: string): boolean => status === "OPEN" || status === "REOPENED";

export function worstSeverity(findings: TvFinding[], id: string, openOnly = true): { severity: string | null; open: number } {
  let severity: string | null = null;
  let open = 0;
  for (const f of findings) {
    if (!f.involved.some((i) => i.id === id)) continue;
    const isOpen = findingOpen(f.status);
    if (isOpen) open++;
    if (openOnly && !isOpen) continue;
    const s = f.severity.toLowerCase();
    if (severity === null || (SEVERITY_RANK[s] ?? 0) > (SEVERITY_RANK[severity] ?? 0)) severity = s;
  }
  return { severity, open };
}

export const KEY_KINDS = ["OPENAI_API_KEY", "OPENROUTER_API_KEY", "SELFHOSTED_LLM_KEY", "SELFHOSTED_LLM_URL"] as const;
export type KeyKind = (typeof KEY_KINDS)[number];
export const kindName = (k: KeyKind): string => t(`kind.${k}`);

export const defaultKeyLabel = (project: string, kind: KeyKind): string => `${project.trim() || t("kind.project")} ${kindName(kind)}`;

export function maskKey(kind: KeyKind, hint: string): string {
  if (kind === "SELFHOSTED_LLM_URL") return hint;
  return hint ? `••••${hint}` : "••••";
}

export const CLASS_KINDS = ["requirements", "architecture-doc", "architecture-model", "documentation", "code", "test", "build", "other", "ignored"] as const;

export interface ClassRow { path: string; name: string; type: "dir" | "file"; kind: string; files: number; weak: boolean; source: string | null; reason: string | null; depth: number; }
export interface ClassFile { path: string; kind: string; source: string; reason: string; weak: boolean; }
export interface ClassScan {
  root: string; classifierVersion: number; fileCount: number; kinds: Record<string, number>; ignored: Record<string, string>;
  review: { weak: number; reviewed: boolean; fingerprint: string; reviewedFingerprint: string | null };
  rows: ClassRow[]; files: ClassFile[]; warnings: string[];
}
export interface ClassEdit { path: string; kind: string; }
export type ClassFilter = "all" | "weak" | "changed";
export interface ClassView extends ClassRow { effKind: string; edited: boolean; changed: boolean; isOpen: boolean; stillWeak: boolean; }

const parentOf = (p: string): string => p.slice(0, Math.max(p.lastIndexOf("/"), 0));
const under = (p: string, dir: string): boolean => p.startsWith(`${dir}/`);

export const initialOpen = (rows: ClassRow[]): Set<string> => new Set(rows.filter((r) => r.type === "dir" && r.kind === "mixed").map((r) => r.path));

function editOf(edits: Map<string, string>, path: string): string | undefined {
  for (let p = path; p; p = parentOf(p)) {
    const k = edits.get(p);
    if (k) return k;
  }
  return undefined;
}

export function setEdit(edits: Map<string, string>, rows: ClassRow[], path: string, kind: string): Map<string, string> {
  const next = new Map([...edits].filter(([p]) => !under(p, path)));
  const row = rows.find((r) => r.path === path);
  next.delete(path);
  const inherited = editOf(next, parentOf(path));
  if (inherited ? inherited !== kind : row?.kind !== kind) next.set(path, kind);
  return next;
}

export const editList = (edits: Map<string, string>): ClassEdit[] => [...edits].map(([path, kind]) => ({ path, kind }));

export function classView(rows: ClassRow[], edits: Map<string, string>, open: Set<string>, filter: ClassFilter = "all", search = ""): ClassView[] {
  const leafKinds = new Map<string, Set<string>>();
  for (const r of rows.filter((x) => x.type === "file")) {
    const k = editOf(edits, r.path) ?? r.kind;
    for (let p = parentOf(r.path); p; p = parentOf(p)) leafKinds.set(p, (leafKinds.get(p) ?? new Set()).add(k));
  }
  const all = rows.map((r): ClassView => {
    const own = editOf(edits, r.path);
    const below = leafKinds.get(r.path);
    const effKind = own ?? (r.type === "dir" && below ? (below.size === 1 ? [...below][0] : "mixed") : r.kind);
    const edited = [...edits.keys()].some((e) => e === r.path || under(e, r.path) || under(r.path, e));
    return { ...r, effKind, edited, changed: edited, isOpen: open.has(r.path), stillWeak: r.weak && !own };
  });
  const q = search.trim().toLowerCase();
  if (filter === "all" && !q) {
    const visible = new Set<string>();
    return all.filter((r) => {
      const p = parentOf(r.path);
      const ok = !p || (visible.has(p) && open.has(p));
      if (ok) visible.add(r.path);
      return ok;
    });
  }
  const hit = new Set(all.filter((r) => (filter !== "weak" || r.stillWeak) && (filter !== "changed" || r.changed) && (!q || r.path.toLowerCase().includes(q))).map((r) => r.path));
  const keep = new Set<string>();
  for (const p of hit) for (let a = p; a; a = parentOf(a)) keep.add(a);
  return all.filter((r) => keep.has(r.path)).map((r) => ({ ...r, isOpen: r.type === "dir" && [...keep].some((k) => under(k, r.path)) }));
}

export function unreviewedMessage(files: ClassFile[]): string {
  const weak = files.filter((f) => f.weak);
  const folders = new Set(weak.map((f) => parentOf(f.path)));
  const fileText = weak.length === 1 ? t("unreviewed.filesOne") : t("unreviewed.filesMany", { n: weak.length });
  const folderText = folders.size === 1 ? t("unreviewed.inFolderOne") : t("unreviewed.inFolderMany", { n: folders.size });
  return t(weak.length === 1 ? "unreviewed.wasOne" : "unreviewed.wereMany", { files: fileText, folders: folderText });
}

export const needsReview = (s: Pick<ClassScan, "review">): boolean => s.review.weak > 0 && !s.review.reviewed;

export const classSummary = (s: ClassScan): string =>
  t("class.summary", {
    count: formatNumber(s.fileCount),
    version: s.classifierVersion,
    review: s.review.weak === 0 ? t("class.summaryNothing") : s.review.reviewed ? t("class.summaryDone") : t("class.summaryOpen", { message: unreviewedMessage(s.files) }),
  });
