import { formatDateTime, t } from "../i18n";
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
    clear(list, keys.length ? table([t("keys.colLabel"), t("keys.colKind"), t("keys.colValue"), t("keys.colLastUsed"), ""], keys.map((k) => [
      k.label, kindName(k.kind), maskKey(k.kind, k.hint), k.lastUsed ? formatDateTime(new Date(k.lastUsed * 1000)) : "",
      h("span", { class: "row" },
        h("button", { class: "secondary", onclick: () => run(async () => {
          await navigator.clipboard.writeText(await cmd<string>("get_key_value", { id: k.id }));
          toast(t("keys.copied"));
        }) }, t("keys.copy")),
        h("button", { class: "secondary", onclick: () => {
          const v = prompt(t("keys.newLabel"), k.label);
          if (v !== null) void run(() => cmd("rename_key", { id: k.id, label: v }));
        } }, t("keys.rename")),
        h("button", { class: "secondary", onclick: () => {
          if (confirm(t("keys.confirmDelete", { label: k.label }))) void run(() => cmd("delete_key", { id: k.id }));
        } }, t("keys.delete"))),
    ])) : h("p", { class: "muted" }, t("keys.none")));
  }
  const kind = select([...KEY_KINDS], KEY_KINDS[0]);
  const label = h("input", { type: "text", maxlength: "100", placeholder: t("keys.labelPlaceholder") });
  const value = h("input", { type: "password", autocomplete: "off", placeholder: t("keys.valuePlaceholder") });
  kind.addEventListener("change", () => { value.type = kind.value === "SELFHOSTED_LLM_URL" ? "text" : "password"; });
  clear(root, h("h1", {}, t("common.keys")),
    h("p", {}, t("keys.warning")), list, msg,
    h("div", { class: "row" }, kind, label, value, h("button", { onclick: () => run(async () => {
      await cmd("save_key", { kind: kind.value as KeyKind, label: label.value, value: value.value });
      label.value = value.value = "";
    }) }, t("keys.add"))),
    back ? h("div", { class: "row" }, h("button", { class: "secondary", onclick: back }, t("common.back"))) : null);
  await load();
}
