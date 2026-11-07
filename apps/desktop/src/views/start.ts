import { cmd, Project, SettingsInput, SettingsView } from "../api";
import { slugify, validSlug } from "../logic";
import { clear, field, h, select, showError, toast } from "./dom";

export async function renderStart(root: HTMLElement, open: (p: Project) => void): Promise<void> {
  let selected: Project | undefined;
  const list = h("div", {});
  const open_ = h("button", { disabled: true, onclick: () => selected && open(selected) }, "Open");
  const remove = h("button", { disabled: true, class: "secondary", onclick: async () => {
    if (!selected) return;
    await cmd("delete_project", { projectId: selected.id }).catch((e) => toast(String(e), "error"));
    load();
  } }, "Remove");
  async function load(): Promise<void> {
    selected = undefined;
    open_.disabled = remove.disabled = true;
    const projects = await cmd<Project[]>("list_projects");
    clear(
      list,
      projects.length ? h("button", { class: "continue", onclick: () => open(projects[0]) }, `Continue ${projects[0].name}`) : null,
      projects.length === 0 ? h("p", { class: "muted" }, "No project yet. Create one with New project.") : null,
      ...projects.map((p) => {
        const row: HTMLElement = h("div", { class: "project-row", onclick: () => {
          selected = p;
          open_.disabled = remove.disabled = false;
          list.querySelectorAll(".project-row").forEach((r) => r.classList.remove("selected"));
          row.classList.add("selected");
        }, ondblclick: () => open(p) },
        h("strong", {}, p.name), h("span", { class: "muted" }, ` ${p.id} · ${p.mode} · ${p.url}`));
        return row;
      }),
    );
  }
  clear(root, h("h1", {}, "SANDMAN"), list,
    h("div", { class: "row" }, h("button", { onclick: () => wizard(root, open, () => renderStart(root, open)) }, "New project"), open_, remove));
  await load().catch((e) => showError(root, e));
}

function secretInput(name: string, set: boolean): HTMLInputElement {
  return h("input", { type: "password", name, autocomplete: "off", placeholder: set ? "(set, leave empty to keep)" : "" });
}

async function wizard(root: HTMLElement, open: (p: Project) => void, back: () => void): Promise<void> {
  const defaults = await cmd<SettingsView>("get_defaults").catch(() => ({ selfhostedLlmUrl: "", corenlpMode: "", keysSet: [] } as SettingsView));
  const name = h("input", { type: "text", maxlength: "100" });
  const id = h("input", { type: "text", maxlength: "63" });
  let idTouched = false;
  id.addEventListener("input", () => (idTouched = true));
  name.addEventListener("input", () => { if (!idTouched) id.value = slugify(name.value); });
  const mode = select(["local", "remote"], "local");
  const checkout = h("input", { type: "text", placeholder: "path of the SANDMAN checkout" });
  const keys = {
    openaiApiKey: secretInput("openai", defaults.keysSet.includes("openaiApiKey")),
    openrouterApiKey: secretInput("openrouter", defaults.keysSet.includes("openrouterApiKey")),
    selfhostedLlmKey: secretInput("selfhosted", defaults.keysSet.includes("selfhostedLlmKey")),
  };
  const llmUrl = h("input", { type: "text", value: defaults.selfhostedLlmUrl, placeholder: "http://ollama:11434" });
  const corenlp = select(["remote", "local"], defaults.corenlpMode || "remote");
  const saveDefault = h("input", { type: "checkbox" });
  const url = h("input", { type: "text", placeholder: "https://sandman.example" });
  const token = h("input", { type: "password", autocomplete: "off" });
  const msg = h("div", {});
  const localBox = h("div", {},
    field("Checkout folder", h("div", { class: "row" }, checkout, h("button", { class: "secondary", type: "button", onclick: async () => {
      const p = await cmd<string | null>("pick_folder");
      if (p) checkout.value = p;
    } }, "Browse"))),
    h("h3", {}, "Keys"),
    field("OpenAI API key", keys.openaiApiKey), field("OpenRouter API key", keys.openrouterApiKey),
    field("Self-hosted model URL", llmUrl), field("Self-hosted model key", keys.selfhostedLlmKey),
    field("CoreNLP mode", corenlp),
    h("label", {}, saveDefault, " Save as default for new projects"));
  const remoteBox = h("div", {}, field("URL", url), field("Token", token, "Stored in the OS keychain."));
  const sync = () => { localBox.hidden = mode.value !== "local"; remoteBox.hidden = mode.value !== "remote"; };
  mode.addEventListener("change", sync);
  sync();
  const create = h("button", { type: "submit" }, "Create");
  const form = h("form", { onsubmit: async (ev: Event) => {
    ev.preventDefault();
    clear(msg);
    if (!validSlug(id.value)) return showError(msg, "Project id must match ^[a-z0-9][a-z0-9-]{1,62}$");
    create.disabled = true;
    try {
      const local = mode.value === "local";
      const warning = await cmd<string | null>("save_project", { project: {
        id: id.value, name: name.value, mode: mode.value, url: local ? "" : url.value, checkout: local ? checkout.value : undefined, lastUsed: 0 } });
      if (local) {
        const settings: SettingsInput = {
          openaiApiKey: keys.openaiApiKey.value, openrouterApiKey: keys.openrouterApiKey.value,
          selfhostedLlmKey: keys.selfhostedLlmKey.value, selfhostedLlmUrl: llmUrl.value, corenlpMode: corenlp.value };
        await cmd("write_local_settings", { checkout: checkout.value, settings, fromDefaults: true });
        if (saveDefault.checked) await cmd("save_defaults", { settings });
      } else if (token.value) await cmd("set_token", { projectId: id.value, token: token.value });
      if (warning) toast(warning, "error");
      open((await cmd<Project[]>("list_projects")).find((p) => p.id === id.value)!);
    } catch (e) {
      showError(msg, e);
      create.disabled = false;
    }
  } }, field("Name", name), field("Id", id, "Lowercase letters, digits and dashes."), field("Mode", mode), localBox, remoteBox, msg,
  h("div", { class: "row" }, create, h("button", { type: "button", class: "secondary", onclick: back }, "Cancel")));
  clear(root, h("h1", {}, "New project"), form);
}
