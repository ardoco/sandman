import { cmd, KeyInfo } from "../api";
import { KEY_KINDS, KeyKind, kindName, maskKey } from "../logic";
import { clear, h, select, showError, table, toast } from "./dom";

export async function renderKeys(root: HTMLElement, back?: () => void): Promise<void> {
  const list = h("div", {});
  const msg = h("div", {});
  const run = async (f: () => Promise<unknown>) => {
    clear(msg);
    try { await f(); } catch (e) { showError(msg, e); }
    await load();
  };
  async function load(): Promise<void> {
    const keys = await cmd<KeyInfo[]>("list_keys").catch((e) => { showError(msg, e); return [] as KeyInfo[]; });
    clear(list, keys.length ? table(["Label", "Kind", "Value", "Last used", ""], keys.map((k) => [
      k.label, kindName(k.kind), maskKey(k.kind, k.hint), k.lastUsed ? new Date(k.lastUsed * 1000).toLocaleString() : "",
      h("span", { class: "row" },
        h("button", { class: "secondary", onclick: () => run(async () => {
          await navigator.clipboard.writeText(await cmd<string>("get_key_value", { id: k.id }));
          toast("Copied.");
        }) }, "Copy"),
        h("button", { class: "secondary", onclick: () => {
          const v = prompt("New label", k.label);
          if (v !== null) void run(() => cmd("rename_key", { id: k.id, label: v }));
        } }, "Rename"),
        h("button", { class: "secondary", onclick: () => {
          if (confirm(`Delete "${k.label}"? This removes the value from the keychain.`)) void run(() => cmd("delete_key", { id: k.id }));
        } }, "Delete")),
    ])) : h("p", { class: "muted" }, "No keys saved yet."));
  }
  const kind = select([...KEY_KINDS], KEY_KINDS[0]);
  const label = h("input", { type: "text", maxlength: "100", placeholder: "label" });
  const value = h("input", { type: "password", autocomplete: "off", placeholder: "value" });
  kind.addEventListener("change", () => { value.type = kind.value === "SELFHOSTED_LLM_URL" ? "text" : "password"; });
  clear(root, h("h1", {}, "Keys"),
    h("p", {}, "Warning: Key values are stored in the OS keychain. Copy puts a value on the clipboard."), list, msg,
    h("div", { class: "row" }, kind, label, value, h("button", { onclick: () => run(async () => {
      await cmd("save_key", { kind: kind.value as KeyKind, label: label.value, value: value.value });
      label.value = value.value = "";
    }) }, "Add")),
    back ? h("div", { class: "row" }, h("button", { class: "secondary", onclick: back }, "Back")) : null);
  await load();
}
