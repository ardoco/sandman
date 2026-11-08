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
  if (status === 200) return "Token accepted.";
  if (status === 401) return "The server rejected this token. Copy the whole secret from the console tokens page. It starts with sdm_.";
  if (status === 403) {
    const detail = problemMessage(status, body);
    return /password change/i.test(detail)
      ? "Password change required. Open the console login, sign in, change the password, then create the token."
      : detail;
  }
  return problemMessage(status, body);
}

export function windowTitle(name: string, stack: string | null): string {
  return stack ? `${name} - ${stack} - SANDMAN` : `${name} - SANDMAN`;
}

export function lastLines(lines: string[], n = 15): string {
  return lines.slice(-n).join("\n");
}

export interface TvElement { id: string; label: string; parentId?: string | null; artifactId?: string; }
export interface TvLink { sourceId: string; targetId: string; approach?: string | null; confidence?: number | null; status: string; }
export interface TvFinding { severity: string; status: string; involved: { id: string; role?: string }[]; }

export const COLUMNS = ["Requirements", "Documentation", "Architecture", "Code", "Tests"];
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
const KIND_NAME: Record<KeyKind, string> = {
  OPENAI_API_KEY: "OpenAI key",
  OPENROUTER_API_KEY: "OpenRouter key",
  SELFHOSTED_LLM_KEY: "self-hosted key",
  SELFHOSTED_LLM_URL: "self-hosted URL",
};
export const kindName = (k: KeyKind): string => KIND_NAME[k];

export const defaultKeyLabel = (project: string, kind: KeyKind): string => `${project.trim() || "project"} ${KIND_NAME[kind]}`;

export function maskKey(kind: KeyKind, hint: string): string {
  if (kind === "SELFHOSTED_LLM_URL") return hint;
  return hint ? `••••${hint}` : "••••";
}
