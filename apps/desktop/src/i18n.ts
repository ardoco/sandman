import { de } from "./de";
import { en, Key } from "./en";
import type { Language } from "./logic";

export type { Key };
type Params = Record<string, string | number>;

const CATALOGUES: Record<Language, Record<string, string>> = { en, de };
let current: Language = "en";

export const setLanguage = (l: Language): void => { current = l; };
export const getLanguage = (): Language => current;

export function translate(key: string, catalogue: Record<string, string>, fallback: Record<string, string>, params: Params = {}): string {
  const text = catalogue[key] ?? fallback[key] ?? key;
  return text.replace(/\{(\w+)\}/g, (m, name: string) => (name in params ? String(params[name]) : m));
}

export const t = (key: Key, params?: Params): string => translate(key, CATALOGUES[current], en, params);

export const formatDateTime = (d: Date): string => new Intl.DateTimeFormat(current, { dateStyle: "medium", timeStyle: "short" }).format(d);
export const formatNumber = (n: number): string => new Intl.NumberFormat(current).format(n);

export function localizeError(raw: string): string {
  const [mark, code, json, ...rest] = raw.split("\u001f");
  if (mark !== "" || rest.length === 0) return raw;
  const text = rest.join("\u001f");
  const key = `err.${code}`;
  if (!(key in en)) return text;
  let params: Params = {};
  try { params = JSON.parse(json); } catch { params = {}; }
  return t(key as Key, params);
}
