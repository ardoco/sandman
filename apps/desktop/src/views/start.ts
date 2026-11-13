import { cmd, connectSystem, hasToken, loginConnect, Project, saveToken, SettingsInput, SettingsView } from "../api";
import { t } from "../i18n";
import { connectSummary, localProject, LoginMode, loginFields, prefillUrl, slugify, validSlug } from "../logic";
import { clear, field, h, select, showError, toast } from "./dom";
import { keyField } from "./keyfield";
import { renderAppSettings } from "./appsettings";
import { renderKeys } from "./keys";
import { secretsBox } from "./secrets";

let loginMode: LoginMode = "password";

export async function renderStart(root: HTMLElement, open: (p: Project) => void): Promise<void> {
  let selected: Project | undefined;
  const list = h("div", {});
  const open_ = h("button", { disabled: true, onclick: () => selected && open(selected) }, t("start.open"));
  const remove = h("button", { disabled: true, class: "secondary", onclick: async () => {
    if (!selected) return;
    await cmd("delete_project", { projectId: selected.id }).catch((e) => toast(String(e), "error"));
    load();
  } }, t("start.remove"));
  const connectUrl = h("input", { type: "text", placeholder: "https://sandman.example" });
  const connectUser = h("input", { type: "text", autocomplete: "username" });
  const connectPassword = h("input", { type: "password", autocomplete: "off" });
  const connectToken = h("input", { type: "password", autocomplete: "off" });
  const connectMsg = h("div", {});
  const connect = h("button", { type: "submit" }, t("start.connect"));
  const fields = {
    Username: field(t("start.username"), connectUser),
    Password: field(t("start.password"), connectPassword, t("start.passwordHint")),
    Token: field(t("common.token"), connectToken, t("start.tokenHint")),
  };
  const switchMode = h("button", { type: "button", class: "secondary" }, "");
  const showMode = () => {
    const shown = loginFields(loginMode);
    for (const [name, el] of Object.entries(fields)) el.hidden = !shown.includes(name);
    switchMode.textContent = loginMode === "password" ? t("start.useToken") : t("start.usePassword");
  };
  switchMode.onclick = () => { loginMode = loginMode === "password" ? "token" : "password"; clear(connectMsg); showMode(); };
  showMode();
  const connectForm = h("form", { onsubmit: async (ev: Event) => {
    ev.preventDefault();
    clear(connectMsg);
    connect.disabled = true;
    try {
      const r = loginMode === "password"
        ? await loginConnect(connectUrl.value, connectUser.value, connectPassword.value)
        : await connectSystem(connectUrl.value, connectToken.value);
      connectPassword.value = "";
      if (!r.ok) return showError(connectMsg, r.message);
      connectToken.value = "";
      connectMsg.append(...connectSummary(r.result).map((t) => h("p", {}, t)));
      await load();
    } catch (e) { connectPassword.value = ""; showError(connectMsg, e); } finally { connect.disabled = false; }
  } }, h("h3", {}, t("start.connectTitle")), switchMode, field(t("common.url"), connectUrl), fields.Username, fields.Password, fields.Token, connect, connectMsg);
  const runLocally = async () => {
    const local = localProject(await cmd<Project[]>("list_projects"));
    if (local) open(local);
    else wizard(root, open, () => renderStart(root, open));
  };
  async function load(): Promise<void> {
    selected = undefined;
    open_.disabled = remove.disabled = true;
    const projects = await cmd<Project[]>("list_projects");
    connectUrl.value ||= prefillUrl(projects);
    const missing = new Set<string>();
    for (const p of projects) if (!(await hasToken(p.id).catch(() => true))) missing.add(p.id);
    clear(
      list,
      projects.length ? h("button", { class: "continue", onclick: () => open(projects[0]) }, t("start.continue", { name: projects[0].name })) : null,
      projects.length === 0 ? h("p", { class: "muted" }, t("start.noProject")) : null,
      ...projects.map((p) => {
        const row: HTMLElement = h("div", { class: "project-row", onclick: () => {
          selected = p;
          open_.disabled = remove.disabled = false;
          list.querySelectorAll(".project-row").forEach((r) => r.classList.remove("selected"));
          row.classList.add("selected");
        }, ondblclick: () => open(p) },
        h("strong", {}, p.name), h("span", { class: "muted" }, ` ${p.id} · ${t(`mode.${p.mode}`)} · ${p.url}`),
        missing.has(p.id) ? tokenPrompt(p, load) : null);
        return row;
      }),
    );
  }
  clear(root, h("h1", {}, "SANDMAN"), connectForm,
    h("hr", {}), h("button", { type: "button", onclick: () => void runLocally().catch((e) => showError(root, e)) }, t("start.runLocally")), h("hr", {}), list,
    h("div", { class: "row" }, h("button", { onclick: () => wizard(root, open, () => renderStart(root, open)) }, t("start.newProject")), open_, remove,
      h("button", { class: "secondary", onclick: () => renderKeys(root, () => renderStart(root, open)) }, t("common.keys")),
      h("button", { class: "secondary", onclick: () => void renderAppSettings(root, { back: () => renderStart(root, open) }) }, t("common.settings"))));
  await load().catch((e) => showError(root, e));
}

function tokenPrompt(p: Project, reload: () => Promise<void>): HTMLElement {
  const box: HTMLElement = h("span", { class: "token-needed", onclick: (ev: Event) => ev.stopPropagation() });
  const ask = () => {
    const input = h("input", { type: "password", autocomplete: "off", placeholder: t("start.tokenFor", { url: p.url }) });
    clear(box, input, h("button", { type: "button", onclick: async () => {
      const r = await saveToken(p.id, input.value.trim()).catch((e) => ({ ok: false, message: String(e) }));
      if (r.ok) await reload();
      else toast(r.message, "error");
    } }, t("common.save")));
    input.focus();
  };
  clear(box, h("button", { type: "button", class: "secondary", onclick: ask }, t("start.tokenNeeded")));
  return box;
}

function secretInput(name: string, set: boolean): HTMLInputElement {
  return h("input", { type: "password", name, autocomplete: "off", placeholder: set ? t("common.keepPlaceholder") : "" });
}

async function wizard(root: HTMLElement, open: (p: Project) => void, back: () => void): Promise<void> {
  const defaults = await cmd<SettingsView>("get_defaults").catch(() => ({ selfhostedLlmUrl: "", corenlpMode: "", keysSet: [] } as SettingsView));
  const name = h("input", { type: "text", maxlength: "100" });
  const id = h("input", { type: "text", maxlength: "63" });
  let idTouched = false;
  id.addEventListener("input", () => (idTouched = true));
  name.addEventListener("input", () => { if (!idTouched) id.value = slugify(name.value); });
  const mode = select([["local", t("mode.local")], ["remote", t("mode.remote")]], "local");
  const checkout = h("input", { type: "text", placeholder: t("wizard.checkoutPlaceholder") });
  const pn = () => name.value;
  const kf = {
    openaiApiKey: keyField(t("key.openaiApiKey"), "OPENAI_API_KEY", secretInput("openai", defaults.keysSet.includes("openaiApiKey")), pn),
    openrouterApiKey: keyField(t("key.openrouterApiKey"), "OPENROUTER_API_KEY", secretInput("openrouter", defaults.keysSet.includes("openrouterApiKey")), pn),
    selfhostedLlmKey: keyField(t("key.selfhostedLlmKey"), "SELFHOSTED_LLM_KEY", secretInput("selfhosted", defaults.keysSet.includes("selfhostedLlmKey")), pn),
    selfhostedLlmUrl: keyField(t("key.selfhostedLlmUrl"), "SELFHOSTED_LLM_URL",
      h("input", { type: "text", value: defaults.selfhostedLlmUrl, placeholder: "http://ollama:11434" }), pn),
  };
  const keys = { openaiApiKey: kf.openaiApiKey.input, openrouterApiKey: kf.openrouterApiKey.input, selfhostedLlmKey: kf.selfhostedLlmKey.input };
  const llmUrl = kf.selfhostedLlmUrl.input;
  const secrets = secretsBox();
  checkout.addEventListener("change", () => void secrets.check(checkout.value));
  const corenlp = select([["remote", t("mode.remote")], ["local", t("mode.local")]], defaults.corenlpMode || "remote");
  const saveDefault = h("input", { type: "checkbox" });
  const url = h("input", { type: "text", placeholder: "https://sandman.example" });
  const token = h("input", { type: "password", autocomplete: "off" });
  const msg = h("div", {});
  const localBox = h("div", {},
    field(t("wizard.checkout"), h("div", { class: "row" }, checkout, h("button", { class: "secondary", type: "button", onclick: async () => {
      const p = await cmd<string | null>("pick_folder");
      if (p) checkout.value = p;
    } }, t("wizard.browse")))),
    secrets.el,
    h("h3", {}, t("settings.keys")),
    kf.openaiApiKey.el, kf.openrouterApiKey.el, kf.selfhostedLlmUrl.el, kf.selfhostedLlmKey.el,
    field(t("settings.coreNlpMode"), corenlp),
    h("label", {}, saveDefault, t("wizard.saveDefault")));
  const remoteBox = h("div", {}, field(t("common.url"), url), field(t("common.token"), token, t("common.keychainHint")));
  const sync = () => { localBox.hidden = mode.value !== "local"; remoteBox.hidden = mode.value !== "remote"; };
  mode.addEventListener("change", sync);
  sync();
  const create = h("button", { type: "submit" }, t("wizard.create"));
  const form = h("form", { onsubmit: async (ev: Event) => {
    ev.preventDefault();
    clear(msg);
    if (!validSlug(id.value)) return showError(msg, t("wizard.idInvalid"));
    create.disabled = true;
    try {
      const local = mode.value === "local";
      if (local && !(await secrets.check(checkout.value))) throw new Error(t("wizard.secretsMissing"));
      const warning = await cmd<string | null>("save_project", { project: {
        id: id.value, name: name.value, mode: mode.value, url: local ? "" : url.value, checkout: local ? checkout.value : undefined, lastUsed: 0 } });
      if (local) {
        const settings: SettingsInput = {
          openaiApiKey: keys.openaiApiKey.value, openrouterApiKey: keys.openrouterApiKey.value,
          selfhostedLlmKey: keys.selfhostedLlmKey.value, selfhostedLlmUrl: llmUrl.value, corenlpMode: corenlp.value };
        await cmd("write_local_settings", { checkout: checkout.value, settings, fromDefaults: true });
        for (const k of Object.values(kf)) await k.save();
        if (saveDefault.checked) await cmd("save_defaults", { settings });
      } else if (token.value) await cmd("set_token", { projectId: id.value, token: token.value });
      if (warning) toast(warning, "error");
      open((await cmd<Project[]>("list_projects")).find((p) => p.id === id.value)!);
    } catch (e) {
      showError(msg, e);
      create.disabled = false;
    }
  } }, field(t("wizard.name"), name), field(t("wizard.id"), id, t("wizard.idHint")), field(t("wizard.mode"), mode), localBox, remoteBox, msg,
  h("div", { class: "row" }, create, h("button", { type: "button", class: "secondary", onclick: back }, t("common.cancel"))));
  clear(root, h("h1", {}, t("wizard.title")), form);
}
