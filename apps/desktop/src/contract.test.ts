import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { parse } from "yaml";
import { CALLS } from "./api";

const DIR = resolve(__dirname, "../../../contracts/openapi");
const docs = new Map<string, any>();
const load = (file: string) => {
  if (!docs.has(file)) docs.set(file, parse(readFileSync(file, "utf8")));
  return docs.get(file);
};

function deref(node: any, file: string): [any, string] {
  while (node?.$ref) {
    const [f, ptr = ""] = node.$ref.split("#");
    file = f ? resolve(dirname(file), f) : file;
    node = ptr.split("/").filter(Boolean).reduce((n: any, k: string) => n?.[k], load(file));
  }
  return [node, file];
}

function props(node: any, file: string): Set<string> {
  const [n, f] = deref(node, file);
  const out = new Set<string>(Object.keys(n?.properties ?? {}));
  for (const part of n?.allOf ?? []) props(part, f).forEach((k) => out.add(k));
  for (const part of n?.oneOf ?? []) props(part, f).forEach((k) => out.add(k));
  return out;
}

const spec = (name: string) => resolve(DIR, name);
const SPECS = ["query-service.yaml", "inconsistency-agent.yaml", "orchestrator.yaml"].map(spec);

function operations(opId: string): { op: any; file: string }[] {
  return SPECS.flatMap((file) =>
    Object.values<any>(load(file).paths ?? {}).flatMap((item) =>
      Object.values<any>(item).filter((op) => op?.operationId === opId).map((op) => ({ op, file }))));
}

const responseSchema = (op: any, file: string) => {
  const ok = Object.entries<any>(op.responses ?? {}).find(([c]) => c.startsWith("2"));
  return ok?.[1] ? deref(ok[1], file) : undefined;
};

describe("desktop calls match the OpenAPI contracts", () => {
  for (const [opId, call] of Object.entries(CALLS) as [string, any][]) {
    it(opId, () => {
      const qs = load(spec("query-service.yaml")).paths[call.path];
      expect(qs, `${call.path} missing`).toBeDefined();
      const mine = qs[call.method.toLowerCase()];
      expect(mine?.operationId, `${call.method} ${call.path}`).toBe(opId);

      const fileOf = spec("query-service.yaml");
      if (call.body) {
        const [rb, f] = deref(mine.requestBody, fileOf);
        const known = props(rb.content["application/json"].schema, f);
        for (const k of call.body) expect(known, `body field ${k}`).toContain(k);
      }
      if (call.reads) {
        const [resp, f] = responseSchema(mine, fileOf) ?? [undefined, fileOf];
        const known = props(resp?.content?.["application/json"]?.schema, f);
        for (const k of call.reads) expect(known, `response field ${k}`).toContain(k);
      }
      if (call.query) {
        const names = new Set<string>();
        for (const { op, file } of operations(opId))
          for (const p of op.parameters ?? []) names.add(deref(p, file)[0].name);
        for (const k of call.query) expect(names, `query param ${k}`).toContain(k);
      }
    });
  }
});
