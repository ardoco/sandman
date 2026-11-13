import { describe, expect, it } from "vitest";
import { applySettings, resolveLanguage, settingsTabState } from "./logic";

const fakeRoot = () => ({ dataset: {} as Record<string, string>, lang: "" }) as unknown as HTMLElement;

describe("applySettings", () => {
  it("sets theme, font and language on the root element", () => {
    const root = fakeRoot();
    applySettings({ language: "de", theme: "dark", fontSize: "large" }, root, "en-US");
    expect(root.dataset).toEqual({ theme: "dark", font: "large" });
    expect(root.lang).toBe("de");
  });

  it("resolves the system language from the navigator language", () => {
    expect(resolveLanguage("system", "de-DE")).toBe("de");
    expect(resolveLanguage("system", "en-US")).toBe("en");
    expect(resolveLanguage("system", "fr-FR")).toBe("en");
    expect(resolveLanguage("en", "de-DE")).toBe("en");
    expect(resolveLanguage("de", "en-US")).toBe("de");
    const root = fakeRoot();
    applySettings({ language: "system", theme: "system", fontSize: "normal" }, root, "de-AT");
    expect(root.lang).toBe("de");
  });
});

describe("settingsTabState", () => {
  it("opens Project with a project and disables it without one", () => {
    expect(settingsTabState(true)).toEqual({ first: "project", projectDisabled: false });
    expect(settingsTabState(false)).toEqual({ first: "general", projectDisabled: true });
  });
});
