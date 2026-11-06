import { createHash, randomUUID } from "node:crypto";
import canonicalize from "canonicalize";

export const SEP = "\x1f";
const WS = "[ \\t\\n\\r\\f\\v]";
const WS_RUN = new RegExp(`${WS}+`, "g");
const WS_EDGE = new RegExp(`^${WS}+|${WS}+$`, "g");

export function sha256Hex(data: string | Uint8Array): string {
  return createHash("sha256").update(data).digest("hex");
}

export function stableId(prefix: string, ...parts: string[]): string {
  return `${prefix}_${sha256Hex(parts.join(SEP)).slice(0, 32)}`;
}

export function canonicalJson(value: unknown): string {
  const out = canonicalize(value);
  if (out === undefined) throw new TypeError("value has no JSON form");
  return out;
}

const byUtf8 = (a: string, b: string): number => Buffer.compare(Buffer.from(a), Buffer.from(b));

function slug(heading: string): string {
  const lowered = heading.normalize("NFC").replace(/[A-Z]/g, (c) => c.toLowerCase());
  return lowered.replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "") || "section";
}

export function sectionLocalKey(headings: readonly string[], occurrences: readonly number[]): string {
  if (headings.length !== occurrences.length) throw new RangeError("headings and occurrences differ in length");
  return "sec:" + headings.map((h, i) => slug(h) + ((occurrences[i] ?? 1) >= 2 ? `~${occurrences[i]}` : "")).join("/");
}

export function sentenceLocalKey(sectionKey: string, text: string, occurrence: number): string {
  const normalized = text.normalize("NFC").replace(WS_RUN, " ").replace(WS_EDGE, "");
  return `${sectionKey}/${sha256Hex(normalized).slice(0, 12)}${occurrence >= 2 ? `~${occurrence}` : ""}`;
}

// ponytail: toUpperCase().toLowerCase() approximates Unicode full case folding (covers sharp s, final sigma); exotic folds may differ.
export function normalizeConceptName(name: string): string {
  const folded = name.normalize("NFC").toUpperCase().toLowerCase().normalize("NFC");
  return folded.replace(WS_RUN, " ").replace(WS_EDGE, "");
}

export function fingerprint(type: string, detectorId: string, involvedKeys: Iterable<string>): string {
  return sha256Hex([type, detectorId, ...[...involvedKeys].sort(byUtf8)].join(SEP));
}

export function evidenceHash(pairs: Iterable<string>): string {
  return sha256Hex([...new Set(pairs)].sort(byUtf8).join(SEP));
}

export const newRunId = (): string => `run_${randomUUID().replaceAll("-", "")}`;
export const newWorkflowId = (): string => `wf_${randomUUID().replaceAll("-", "")}`;
