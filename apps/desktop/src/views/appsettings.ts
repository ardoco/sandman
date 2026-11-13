import { AppSettings, Ctx, loadAppSettings, saveAppSettings } from "../api";
import { applySettings, DEFAULT_SETTINGS, SettingsTab, settingsTabState } from "../logic";
import { clear, field, h, showError, toast } from "./dom";
import { renderSettings } from "./settings";

interface Options { ctx?: Ctx; back?: () => void; }

const choice = (options: [string, string][], value: string, onchange: (v: string) => void): HTMLSelectElement => {
  const el = h("select", {}, ...options.map(([v, label]) => h("option", { value: v }, label)));
  el.value = value;
  el.addEventListener("change", () => onchange(el.value));
  return el;
};

export async function renderAppSettings(root: HTMLElement, { ctx, back }: Options): Promise<void> {
  const { first, projectDisabled } = settingsTabState(!!ctx);
  const nav = h("nav", {});
  const panel = h("div", {});
  const tabs: [SettingsTab, string][] = [["general", "General"], ["project", "Project"]];
  const settings: AppSettings = await loadAppSettings().catch(() => ({ ...DEFAULT_SETTINGS } as AppSettings));

  const update = async (patch: Partial<AppSettings>) => {
    Object.assign(settings, patch);
    applySettings(settings);
    try { await saveAppSettings(settings); toast("Saved."); } catch (e) { toast(String(e instanceof Error ? e.message : e), "error"); }
  };

  const general = () => clear(panel,
    field("Language", choice([["system", "System"], ["en", "English"], ["de", "Deutsch"]], settings.language, (language) => void update({ language: language as AppSettings["language"] }))),
    field("Theme", choice([["system", "System"], ["light", "Light"], ["dark", "Dark"]], settings.theme, (theme) => void update({ theme: theme as AppSettings["theme"] }))),
    field("Font size", choice([["small", "Small"], ["normal", "Normal"], ["large", "Large"]], settings.fontSize, (fontSize) => void update({ fontSize: fontSize as AppSettings["fontSize"] }))));

  const show = async (tab: SettingsTab) => {
    nav.querySelectorAll("button").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
    if (tab === "general") return general();
    const view = h("div", {});
    clear(panel, view);
    try { await renderSettings(view, ctx!); } catch (e) { showError(view, e); }
  };

  nav.append(...tabs.map(([id, label]) => {
    const disabled = id === "project" && projectDisabled;
    return h("button", { class: "tab", "data-tab": id, disabled, onclick: () => void show(id) }, label);
  }));
  clear(root, h("h1", {}, "Settings"), nav, projectDisabled ? h("p", { class: "muted" }, "Open a project first") : null, panel, back ? h("div", { class: "row" }, h("button", { class: "secondary", onclick: back }, "Back")) : null);
  await show(first);
}
