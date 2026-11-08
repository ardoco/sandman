import { invoke } from "@tauri-apps/api/core";
import { KeyKind, problemMessage, tokenMessage } from "./logic";

export interface Project {
  id: string;
  name: string;
  mode: "local" | "remote";
  url: string;
  checkout?: string;
  cliCommand?: string;
  lastUsed: number;
}

export interface SettingsInput {
  openaiApiKey?: string;
  openrouterApiKey?: string;
  selfhostedLlmUrl?: string;
  selfhostedLlmKey?: string;
  corenlpMode?: string;
}
export interface SettingsView {
  selfhostedLlmUrl: string;
  corenlpMode: string;
  keysSet: string[];
}
export interface Lifecycle {
  code: number;
  hint: string | null;
}
export interface StackState {
  state: "stopped" | "starting" | "running" | "partial" | "remote";
  ready: boolean;
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export async function cmd<T>(name: string, args: Record<string, unknown> = {}): Promise<T> {
  try {
    return await invoke<T>(name, args);
  } catch (e) {
    throw new Error(String(e));
  }
}

export async function api<T = any>(
  projectId: string,
  method: "GET" | "POST",
  path: string,
  query?: Record<string, string | undefined>,
  body?: unknown,
): Promise<T> {
  const pairs = Object.entries(query ?? {}).filter((kv): kv is [string, string] => !!kv[1]);
  const r = await cmd<{ status: number; body: unknown }>("api", { projectId, method, path, query: pairs, body });
  if (r.status >= 400) throw new ApiError(r.status, problemMessage(r.status, r.body));
  return r.body as T;
}

export const CALLS = {
  me: { method: "GET", path: "/v1/auth/me", reads: ["username", "globalRoles", "disabled"] },
  queryCypher: { method: "POST", path: "/v1/query/cypher", body: ["projectId", "cypher", "params", "limit"], reads: ["columns", "rows", "truncated"] },
  queryNl: { method: "POST", path: "/v1/query/nl", body: ["projectId", "question"], reads: ["columns", "rows", "answer", "cypher", "warnings"] },
  listInconsistencies: { method: "GET", path: "/v1/projects/{projectId}/inconsistencies", query: ["status", "severity", "limit", "cursor"], reads: ["items", "counts", "nextCursor"] },
  getInconsistency: { method: "GET", path: "/v1/inconsistencies/{inconsistencyId}", reads: ["title", "severity", "status", "type", "detector", "description", "involved", "evidence", "history", "allowedTransitions"] },
  transitionInconsistency: { method: "POST", path: "/v1/inconsistencies/{inconsistencyId}/transitions", body: ["to", "expectedStatus", "reason", "cause"] },
  reviewTraceLink: { method: "POST", path: "/v1/projects/{projectId}/links/{linkId}/review", body: ["status", "reason"] },
  getTraceView: { method: "GET", path: "/v1/projects/{projectId}/trace-view", reads: ["elements", "links", "findings", "truncated"] },
  getTraceViewElement: { method: "GET", path: "/v1/projects/{projectId}/trace-view/elements/{elementId}", reads: ["element", "links", "findings", "neighbours"] },
  listWorkflows: { method: "GET", path: "/v1/projects/{projectId}/workflows", query: ["limit", "cursor"], reads: ["items", "nextCursor"] },
  createWorkflow: { method: "POST", path: "/v1/projects/{projectId}/workflows", body: ["type"] },
  getWorkflow: { method: "GET", path: "/v1/workflows/{workflowId}", reads: ["id", "status", "steps", "createdAt", "trigger"] },
} as const;

export const cypher = (projectId: string, statement: string, params: Record<string, unknown> = {}, limit = 200) =>
  api<{ columns: string[]; rows: unknown[][]; truncated: boolean }>(projectId, "POST", "/v1/query/cypher", undefined, {
    projectId,
    cypher: statement,
    params,
    limit,
  });

export interface Ctx {
  project: Project;
  go: (tab: string) => void;
}

export const hasToken = (projectId: string) => cmd<boolean>("has_token", { projectId });

export async function saveToken(projectId: string, token: string): Promise<{ ok: boolean; message: string }> {
  const r = await cmd<{ status: number; body: unknown }>("check_token", { projectId, token });
  return { ok: r.status === 200, message: tokenMessage(r.status, r.body) };
}

export interface KeyInfo {
  id: string;
  label: string;
  kind: KeyKind;
  createdAt: number;
  lastUsed: number;
  hint: string;
}
export interface SecretsState {
  present: boolean;
  missing: string[];
}
