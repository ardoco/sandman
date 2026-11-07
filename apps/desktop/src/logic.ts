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
