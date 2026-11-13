import { afterEach, describe, expect, it } from "vitest";
import { de } from "./de";
import { en } from "./en";
import { applySettings, resolveLanguage } from "./logic";
import { getLanguage, setLanguage, t, translate } from "./i18n";

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
