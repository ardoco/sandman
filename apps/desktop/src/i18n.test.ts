import { afterEach, describe, expect, it } from "vitest";
import { de } from "./de";
import { en } from "./en";
import { applySettings, resolveLanguage } from "./logic";
import { readdirSync, readFileSync } from "node:fs";
import { getLanguage, localizeError, setLanguage, t, translate } from "./i18n";

const fakeRoot = () => ({ dataset: {} as Record<string, string>, lang: "" }) as unknown as HTMLElement;

afterEach(() => setLanguage("en"));

describe("i18n", () => {
  it("fills parameters", () => {
    expect(t("start.continue", { name: "Demo" })).toBe("Continue Demo");
    setLanguage("de");
    expect(t("start.continue", { name: "Demo" })).toBe("Demo fortsetzen");
  });

  it("falls back to English and then to the key", () => {
    expect(translate("a", { b: "x" }, { a: "fallback {n}" }, { n: 2 })).toBe("fallback 2");
    expect(translate("missing", {}, {})).toBe("missing");
  });

  it("selects the language system from the navigator language", () => {
    for (const [nav, lang] of [["de-DE", "de"], ["en-US", "en"], ["fr-FR", "en"]] as const) {
      applySettings({ language: "system", theme: "system", fontSize: "normal" }, fakeRoot(), nav);
      expect(getLanguage()).toBe(lang);
      expect(resolveLanguage("system", nav)).toBe(lang);
    }
  });

  it("has the same keys in English and German", () => {
    expect(Object.keys(de).sort()).toEqual(Object.keys(en).sort());
    for (const k of Object.keys(en)) {
      const params = (s: string) => (s.match(/\{\w+\}/g) ?? []).sort();
      expect(params((de as Record<string, string>)[k]), k).toEqual(params((en as Record<string, string>)[k]));
    }
  });
});

describe("localizeError", () => {
  const us = "\u001f";
  const coded = (code: string, params: object, text: string) => `${us}${code}${us}${JSON.stringify(params)}${us}${text}`;

  it("maps known codes with parameters in both languages", () => {
    const raw = coded("port_in_use", { port: 80 }, "English text");
    expect(localizeError(raw)).toBe("Port 80 is in use by another program. Stop that program and try again.");
    setLanguage("de");
    expect(localizeError(raw)).toBe("Port 80 wird von einem anderen Programm verwendet. Beenden Sie das Programm und versuchen Sie es erneut.");
  });

  it("falls back to the English text for unknown codes", () => {
    expect(localizeError(coded("no_such_code", {}, "English text"))).toBe("English text");
  });

  it("returns plain text unchanged", () => {
    expect(localizeError("plain error")).toBe("plain error");
  });

  it("has a catalogue entry for every code used in Rust", () => {
    const dir = new URL("../src-tauri/src/", import.meta.url);
    const codes = new Set<string>();
    for (const f of readdirSync(dir).filter((n) => n.endsWith(".rs"))) {
      for (const m of readFileSync(new URL(f, dir), "utf8").matchAll(/coded\(\s*"([a-z_]+)"/g)) codes.add(m[1]);
    }
    expect(codes.size).toBeGreaterThan(10);
    for (const code of codes) expect(`err.${code}` in en, code).toBe(true);
  });
});

describe("views", () => {
  it("contain no untranslated text literals", () => {
    const dir = new URL("./views/", import.meta.url);
    const literal = /h\("(?:p|h[1-4]|button|label|span|th|td|summary|option|strong)", (?:\{[^}]*\}), "[A-Za-z]/;
    const bad: string[] = [];
    for (const f of readdirSync(dir).filter((n) => n.endsWith(".ts"))) {
      readFileSync(new URL(f, dir), "utf8").split("\n").forEach((line, i) => {
        if (literal.test(line) && !line.includes('"SANDMAN"')) bad.push(`${f}:${i + 1}`);
      });
    }
    expect(bad).toEqual([]);
  });
});

