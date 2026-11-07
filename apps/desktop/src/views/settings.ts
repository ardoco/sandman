import { cmd, Ctx, hasToken, SettingsInput, SettingsView } from "../api";
import { dashboardUrl } from "../logic";
import { clear, field, h, select, showError, toast } from "./dom";

export async function renderSettings(root: HTMLElement, ctx: Ctx): Promise<void> {
  const p = ctx.project;
  const token = h("input", { type: "password", autocomplete: "off", placeholder: (await hasToken(p.id)) ? "(set, leave empty to keep)" : "" });
  const cli = h("input", { type: "text", value: p.cliCommand ?? "", placeholder: "default: uv run --project <checkout> sandman" });
  const msg = h("div", {});
  const open = (u: string) => cmd("open_external", { url: u }).catch((e) => showError(msg, e));
  const save = async (extra?: () => Promise<void>) => {
    clear(msg);
    try {
      if (token.value) { await cmd("set_token", { projectId: p.id, token: token.value }); token.value = ""; }
      await cmd("save_project", { project: { ...p, cliCommand: cli.value || undefined } });
      await extra?.();
      toast("Saved.");
    } catch (e) { showError(msg, e); }
  };
  clear(root, h("p", {}, `${p.mode} project, ${p.url}`), field("Token", token, "Stored in the OS keychain."), field("CLI command", cli));
  let keys: (() => Promise<void>) | undefined;
  if (p.mode === "local" && p.checkout) {
    const s = await cmd<SettingsView>("read_local_settings", { checkout: p.checkout });
    const mk = (n: string) => h("input", { type: "password", autocomplete: "off", placeholder: s.keysSet.includes(n) ? "(set, leave empty to keep)" : "" });
    const k = { openaiApiKey: mk("openaiApiKey"), openrouterApiKey: mk("openrouterApiKey"), selfhostedLlmKey: mk("selfhostedLlmKey") };
    const url = h("input", { type: "text", value: s.selfhostedLlmUrl });
    const nlp = select(["remote", "local"], s.corenlpMode || "remote");
    clear(root, ...Array.from(root.childNodes) as Node[], h("h3", {}, "Keys"),
      field("OpenAI API key", k.openaiApiKey), field("OpenRouter API key", k.openrouterApiKey),
      field("Self-hosted model URL", url), field("Self-hosted model key", k.selfhostedLlmKey), field("CoreNLP mode", nlp),
      h("p", { class: "muted" }, "Restart the stack to apply key changes."));
    keys = () => {
      const settings: SettingsInput = { openaiApiKey: k.openaiApiKey.value, openrouterApiKey: k.openrouterApiKey.value,
        selfhostedLlmKey: k.selfhostedLlmKey.value, selfhostedLlmUrl: url.value, corenlpMode: nlp.value };
      return cmd("write_local_settings", { checkout: p.checkout, settings, fromDefaults: false });
    };
  }
  root.append(h("div", { class: "row" }, h("button", { onclick: () => save(keys) }, "Save"),
    h("button", { class: "secondary", onclick: () => open(p.url) }, "Open console"),
    h("button", { class: "secondary", onclick: () => open(dashboardUrl(p.url)) }, "Open dashboard")), msg);
}
