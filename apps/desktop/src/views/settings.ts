import { cmd, Ctx, hasToken, saveToken, SettingsInput, SettingsView } from "../api";
import { t } from "../i18n";
import { dashboardUrl } from "../logic";
import { clear, field, h, select, showError, toast } from "./dom";
import { keyField } from "./keyfield";
import { secretsBox } from "./secrets";

export async function renderSettings(root: HTMLElement, ctx: Ctx): Promise<void> {
  const p = ctx.project;
  const token = h("input", { type: "password", autocomplete: "off", placeholder: (await hasToken(p.id)) ? t("common.keepPlaceholder") : "" });
  const cli = h("input", { type: "text", value: p.cliCommand ?? "", placeholder: t("settings.cliPlaceholder") });
  const msg = h("div", {});
  const open = (u: string) => cmd("open_external", { url: u }).catch((e) => showError(msg, e));
  const save = async (extra?: () => Promise<void>) => {
    clear(msg);
    try {
      if (token.value) {
        const r = await saveToken(p.id, token.value.trim());
        if (!r.ok) return showError(msg, r.message);
        token.value = "";
      }
      await cmd("save_project", { project: { ...p, cliCommand: cli.value || undefined } });
      await extra?.();
      toast(t("common.saved"));
    } catch (e) { showError(msg, e); }
  };
  clear(root, h("h3", {}, t("settings.connection")), h("p", {}, t("settings.projectLine", { mode: t(`mode.${p.mode}`), url: p.url })),
    field(t("common.token"), token, t("common.keychainHint")),
    h("h3", {}, t("settings.localStack")), field(t("settings.cliCommand"), cli));
  let keys: (() => Promise<void>) | undefined;
  if (p.mode === "local" && p.checkout) {
    const s = await cmd<SettingsView>("read_local_settings", { checkout: p.checkout });
    const mk = (n: string) => h("input", { type: "password", autocomplete: "off", placeholder: s.keysSet.includes(n) ? t("common.keepPlaceholder") : "" });
    const pn = () => p.name;
    const kf = {
      openaiApiKey: keyField(t("key.openaiApiKey"), "OPENAI_API_KEY", mk("openaiApiKey"), pn),
      openrouterApiKey: keyField(t("key.openrouterApiKey"), "OPENROUTER_API_KEY", mk("openrouterApiKey"), pn),
      selfhostedLlmKey: keyField(t("key.selfhostedLlmKey"), "SELFHOSTED_LLM_KEY", mk("selfhostedLlmKey"), pn),
      selfhostedLlmUrl: keyField(t("key.selfhostedLlmUrl"), "SELFHOSTED_LLM_URL", h("input", { type: "text", value: s.selfhostedLlmUrl }), pn),
    };
    const k = { openaiApiKey: kf.openaiApiKey.input, openrouterApiKey: kf.openrouterApiKey.input, selfhostedLlmKey: kf.selfhostedLlmKey.input };
    const url = kf.selfhostedLlmUrl.input;
    const secrets = secretsBox();
    void secrets.check(p.checkout);
    const nlp = select([["remote", t("mode.remote")], ["local", t("mode.local")]], s.corenlpMode || "remote");
    clear(root, ...Array.from(root.childNodes) as Node[], secrets.el, h("h3", {}, t("settings.keys")),
      kf.openaiApiKey.el, kf.openrouterApiKey.el, kf.selfhostedLlmUrl.el, kf.selfhostedLlmKey.el, field(t("settings.coreNlpMode"), nlp),
      h("p", { class: "muted" }, t("settings.restartHint")));
    keys = async () => {
      const settings: SettingsInput = { openaiApiKey: k.openaiApiKey.value, openrouterApiKey: k.openrouterApiKey.value,
        selfhostedLlmKey: k.selfhostedLlmKey.value, selfhostedLlmUrl: url.value, corenlpMode: nlp.value };
      await cmd("write_local_settings", { checkout: p.checkout, settings, fromDefaults: false });
      for (const f of Object.values(kf)) await f.save();
    };
  }
  root.append(h("div", { class: "row" }, h("button", { onclick: () => save(keys) }, t("common.save"))), msg, h("h3", {}, t("settings.links")),
    h("div", { class: "row" },
      h("button", { class: "secondary", onclick: () => open(p.url) }, t("settings.openConsole")),
      h("button", { class: "secondary", onclick: () => open(dashboardUrl(p.url)) }, t("settings.openDashboard"))));
}
