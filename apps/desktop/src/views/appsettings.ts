import { AppSettings, cmd, Ctx, loadAppSettings, Project, saveAppSettings } from "../api";
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
    if (languageChanged) { ctx?.onLanguage?.(); void renderAppSettings(root, { ctx, back, tab: current }); }
  };

  const systems = h("div", {});
  const showSystems = async () => {
    const remote = (await cmd<Project[]>("list_projects").catch(() => [] as Project[])).filter((p) => p.mode === "remote");
    const urls = [...new Set(remote.map((p) => p.url))];
    clear(systems, ...(urls.length ? urls.map((url) => {
      const count = remote.filter((p) => p.url === url).length;
      return h("div", { class: "row" }, h("span", {}, t("settings.systemLine", { url, count })),
        h("button", { class: "secondary", onclick: async () => {
          if (!confirm(t("settings.forgetConfirm", { url, count }))) return;
          try { await cmd("forget_system", { url }); toast(t("settings.forgotten")); } catch (e) { toast(String(e instanceof Error ? e.message : e), "error"); }
          await showSystems();
        } }, t("settings.forget")));
    }) : [h("p", { class: "muted" }, t("settings.noSystems"))]));
  };
  const checkout = h("input", { type: "text", value: settings.defaultCheckout ?? "", placeholder: t("wizard.checkoutPlaceholder") });
  checkout.addEventListener("change", () => void update({ defaultCheckout: checkout.value }));
  const version = h("span", {});
  void cmd<string>("app_version").then((v) => { version.textContent = v; }).catch(() => undefined);

  const general = () => { void showSystems(); clear(panel,
    field(t("settings.language"), choice([["system", t("settings.languageSystem")], ["en", "English"], ["de", "Deutsch"]], settings.language, (language) => void update({ language: language as AppSettings["language"] }))),
    field(t("settings.theme"), choice([["system", t("settings.themeSystem")], ["light", t("settings.themeLight")], ["dark", t("settings.themeDark")]], settings.theme, (theme) => void update({ theme: theme as AppSettings["theme"] }))),
    field(t("settings.fontSize"), choice([["small", t("settings.fontSmall")], ["normal", t("settings.fontNormal")], ["large", t("settings.fontLarge")]], settings.fontSize, (fontSize) => void update({ fontSize: fontSize as AppSettings["fontSize"] }))),
    field(t("settings.defaultCheckout"), h("div", { class: "row" }, checkout, h("button", { class: "secondary", type: "button", onclick: async () => {
      const p = await cmd<string | null>("pick_folder");
      if (p) { checkout.value = p; void update({ defaultCheckout: p }); }
    } }, t("wizard.browse")))),
    h("h3", {}, t("settings.systems")), systems,
    h("p", { class: "muted" }, t("settings.version"), " ", version));};

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
