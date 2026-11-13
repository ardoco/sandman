import { cmd, Ctx, hasToken, saveToken, SettingsInput, SettingsView } from "../api";
import { dashboardUrl } from "../logic";
import { clear, field, h, select, showError, toast } from "./dom";
import { keyField } from "./keyfield";
import { secretsBox } from "./secrets";

export async function renderSettings(root: HTMLElement, ctx: Ctx): Promise<void> {
  const p = ctx.project;
  const token = h("input", { type: "password", autocomplete: "off", placeholder: (await hasToken(p.id)) ? "(set, leave empty to keep)" : "" });
  const cli = h("input", { type: "text", value: p.cliCommand ?? "", placeholder: "default: uv run --project <checkout> sandman" });
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
      toast("Saved.");
    } catch (e) { showError(msg, e); }
  };
  clear(root, h("h3", {}, "Connection"), h("p", {}, `${p.mode} project, ${p.url}`), field("Token", token, "Stored in the OS keychain."),
    h("h3", {}, "Local stack"), field("CLI command", cli));
  let keys: (() => Promise<void>) | undefined;
  if (p.mode === "local" && p.checkout) {
    const s = await cmd<SettingsView>("read_local_settings", { checkout: p.checkout });
    const mk = (n: string) => h("input", { type: "password", autocomplete: "off", placeholder: s.keysSet.includes(n) ? "(set, leave empty to keep)" : "" });
    const pn = () => p.name;
    const kf = {
      openaiApiKey: keyField("OpenAI API key", "OPENAI_API_KEY", mk("openaiApiKey"), pn),
      openrouterApiKey: keyField("OpenRouter API key", "OPENROUTER_API_KEY", mk("openrouterApiKey"), pn),
      selfhostedLlmKey: keyField("Self-hosted model key", "SELFHOSTED_LLM_KEY", mk("selfhostedLlmKey"), pn),
      selfhostedLlmUrl: keyField("Self-hosted model URL", "SELFHOSTED_LLM_URL", h("input", { type: "text", value: s.selfhostedLlmUrl }), pn),
    };
    const k = { openaiApiKey: kf.openaiApiKey.input, openrouterApiKey: kf.openrouterApiKey.input, selfhostedLlmKey: kf.selfhostedLlmKey.input };
    const url = kf.selfhostedLlmUrl.input;
    const secrets = secretsBox();
    void secrets.check(p.checkout);
    const nlp = select(["remote", "local"], s.corenlpMode || "remote");
    clear(root, ...Array.from(root.childNodes) as Node[], secrets.el, h("h3", {}, "Keys"),
      kf.openaiApiKey.el, kf.openrouterApiKey.el, kf.selfhostedLlmUrl.el, kf.selfhostedLlmKey.el, field("CoreNLP mode", nlp),
      h("p", { class: "muted" }, "Restart the stack to apply key changes."));
    keys = async () => {
      const settings: SettingsInput = { openaiApiKey: k.openaiApiKey.value, openrouterApiKey: k.openrouterApiKey.value,
        selfhostedLlmKey: k.selfhostedLlmKey.value, selfhostedLlmUrl: url.value, corenlpMode: nlp.value };
      await cmd("write_local_settings", { checkout: p.checkout, settings, fromDefaults: false });
      for (const f of Object.values(kf)) await f.save();
    };
  }
  root.append(h("div", { class: "row" }, h("button", { onclick: () => save(keys) }, "Save")), msg, h("h3", {}, "Links"),
    h("div", { class: "row" },
      h("button", { class: "secondary", onclick: () => open(p.url) }, "Open console"),
      h("button", { class: "secondary", onclick: () => open(dashboardUrl(p.url)) }, "Open dashboard")));
}
