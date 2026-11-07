import { invoke } from "@tauri-apps/api/core";
import { problemMessage } from "./logic";

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
