import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { DEFAULT_CONTRACTS_DIR } from "../src/config.ts";
import { Contracts } from "../src/contracts.ts";
import { eventToJson, eventFromJson } from "../src/events.ts";
import {
  canonicalJson, evidenceHash, fingerprint, normalizeConceptName, sectionLocalKey, sentenceLocalKey, sha256Hex, stableId,
} from "../src/ids.ts";

/* eslint-disable @typescript-eslint/no-explicit-any */
const vectors: any = JSON.parse(readFileSync(join(DEFAULT_CONTRACTS_DIR, "conformance/vectors.json"), "utf-8"));
const contracts = new Contracts(DEFAULT_CONTRACTS_DIR);

describe("conformance vectors", () => {
  it.each(vectors.stableId.cases as any[])("stableId $expected", (c: any) => {
    expect(stableId(c.prefix, ...c.parts)).toBe(c.expected);
  });
  it.each(vectors.sectionKey.cases as any[])("sectionKey $expected", (c: any) => {
    expect(sectionLocalKey(c.headings, c.occurrences)).toBe(c.expected);
  });
  it.each(vectors.sentenceKey.cases as any[])("sentenceKey $expected", (c: any) => {
    expect(sentenceLocalKey(c.sectionLocalKey, c.text, c.occurrence)).toBe(c.expected);
  });
  it.each(vectors.conceptName.cases as any[])("conceptName $expected", (c: any) => {
    expect(normalizeConceptName(c.name)).toBe(c.expected);
  });
  it.each(vectors.fingerprint.cases as any[])("fingerprint $expected", (c: any) => {
    const fp = fingerprint(c.input.type, c.input.detector, c.input.involved);
    expect(fp).toBe(c.expected);
    if (c.expectedInconsistencyId) expect(stableId("inc", c.projectId, fp)).toBe(c.expectedInconsistencyId);
  });
  it.each(vectors.evidenceHash.cases as any[])("evidenceHash $name", (c: any) => {
    const elements = [...c.involved.elements, ...(c.involved.links ?? []).flatMap((l: any) => l.endpoints)];
    const pairs = elements.map((e: any) => `${e.key}=${e.contentHash}`);
    expect([...new Set(pairs)].sort()).toEqual(c.sortedPairs);
    expect(evidenceHash(pairs)).toBe(c.expected);
  });
  it.each(vectors.ingestionId.cases as any[])("ingestionId $expected", (c: any) => {
    expect(stableId("ing", c.input.projectId, String(c.input.seq))).toBe(c.expected);
  });
  it.each(vectors.traceLinkId.cases as any[])("traceLinkId $expected", (c: any) => {
    const i = c.input;
    expect(stableId("tl", i.approach, i.approachVersion, i.configHash, i.sourceId, i.targetId)).toBe(c.expected);
  });
  it.each(vectors.inputKey.cases as any[])("inputKey $name", (c: any) => {
    expect(canonicalJson(c.object)).toBe(c.canonical);
    expect(sha256Hex(c.canonical)).toBe(c.expected);
  });
  it.each(vectors.actionKey.cases as any[])("actionKey $name", (c: any) => {
    expect(canonicalJson(c.object)).toBe(c.canonical);
    expect(sha256Hex(c.canonical)).toBe(c.expected);
  });
  it.each(vectors.cassetteKey.cases as any[])("cassetteKey $endpoint", (c: any) => {
    const { user: _u, stream: _s, stream_options: _o, ...body } = c.request;
    expect(canonicalJson(body)).toBe(c.canonical);
    expect(sha256Hex(c.canonical)).toBe(c.expectedKey);
  });
  it.each(vectors.eventEnvelope.cases as any[])("eventEnvelope valid=$valid", (c: any) => {
    expect(contracts.isValid("events.schema.json", c.event)).toBe(c.valid);
    if (c.valid) expect(eventToJson(eventFromJson(c.event))).toEqual(c.event);
  });
});
