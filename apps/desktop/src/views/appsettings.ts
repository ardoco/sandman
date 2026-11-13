import { AppSettings, Ctx, loadAppSettings, saveAppSettings } from "../api";
import { t } from "../i18n";
import { applySettings, DEFAULT_SETTINGS, SettingsTab, settingsTabState } from "../logic";
import { clear, field, h, showError, toast } from "./dom";
import { renderSettings } from "./settings";

interface Options { ctx?: Ctx; back?: () => void; tab?: SettingsTab; }

const choice = (options: [string, string][], value: string, onchange: (v: string) => void): HTMLSelectElement => {
  const el = h("select", {}, ...options.map(([v, label]) => h("option", { value: v }, label)));
  el.value = value;
  el.addEventListener("change", () => onchange(el.value));
  return el;
};

export async function renderAppSettings(root: HTMLElement, { ctx, back, tab }: Options): Promise<void> {
  const { first, projectDisabled } = settingsTabState(!!ctx);
  const nav = h("nav", {});
  const panel = h("div", {});
  const tabs: [SettingsTab, string][] = [["general", t("settings.tabGeneral")], ["project", t("settings.tabProject")]];
  const settings: AppSettings = await loadAppSettings().catch(() => ({ ...DEFAULT_SETTINGS } as AppSettings));

  const update = async (patch: Partial<AppSettings>) => {
    const languageChanged = patch.language !== undefined && patch.language !== settings.language;
    Object.assign(settings, patch);
    applySettings(settings);
    try { await saveAppSettings(settings); toast(t("common.saved")); } catch (e) { toast(String(e instanceof Error ? e.message : e), "error"); }
    if (languageChanged) void renderAppSettings(root, { ctx, back, tab: current });
  };

  const general = () => clear(panel,
    field(t("settings.language"), choice([["system", t("settings.languageSystem")], ["en", "English"], ["de", "Deutsch"]], settings.language, (language) => void update({ language: language as AppSettings["language"] }))),
    field(t("settings.theme"), choice([["system", t("settings.themeSystem")], ["light", t("settings.themeLight")], ["dark", t("settings.themeDark")]], settings.theme, (theme) => void update({ theme: theme as AppSettings["theme"] }))),
    field(t("settings.fontSize"), choice([["small", t("settings.fontSmall")], ["normal", t("settings.fontNormal")], ["large", t("settings.fontLarge")]], settings.fontSize, (fontSize) => void update({ fontSize: fontSize as AppSettings["fontSize"] }))));

  let current: SettingsTab = tab ?? first;
  const show = async (next: SettingsTab) => {
    current = next;
    nav.querySelectorAll("button").forEach((b) => b.classList.toggle("active", b.dataset.tab === next));
    if (next === "general") return general();
    const view = h("div", {});
    clear(panel, view);
    try { await renderSettings(view, ctx!); } catch (e) { showError(view, e); }
  };

  nav.append(...tabs.map(([id, label]) => {
    const disabled = id === "project" && projectDisabled;
    return h("button", { class: "tab", "data-tab": id, disabled, onclick: () => void show(id) }, label);
  }));
  clear(root, h("h1", {}, t("settings.title")), nav, projectDisabled ? h("p", { class: "muted" }, t("settings.openProjectFirst")) : null, panel, back ? h("div", { class: "row" }, h("button", { class: "secondary", onclick: back }, t("common.back"))) : null);
  await show(current);
}
