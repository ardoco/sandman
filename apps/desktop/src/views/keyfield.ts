import { t } from "../i18n";
import { cmd, KeyInfo } from "../api";
import { defaultKeyLabel, KeyKind, maskKey } from "../logic";
import { field, h, toast } from "./dom";

export interface KeyField {
  el: HTMLElement;
  input: HTMLInputElement;
  save: () => Promise<void>;
}

export function keyField(title: string, kind: KeyKind, input: HTMLInputElement, projectName: () => string): KeyField {
  const pick = h("select", {}, h("option", { value: "" }, t("keyfield.fromStore")));
  const saveBox = h("input", { type: "checkbox", checked: true });
  const label = h("input", { type: "text", maxlength: "100" });
  let touched = false;
  label.addEventListener("input", () => (touched = true));
  const fill = () => { if (!touched) label.value = defaultKeyLabel(projectName(), kind); };
  fill();
  input.addEventListener("focus", fill);
  void cmd<KeyInfo[]>("list_keys").then((list) => pick.append(
    ...list.filter((k) => k.kind === kind).map((k) => h("option", { value: k.id }, `${k.label} ${maskKey(kind, k.hint)}`)))).catch(() => undefined);
  pick.addEventListener("change", async () => {
    if (!pick.value) return;
    try { input.value = await cmd<string>("get_key_value", { id: pick.value }); } catch (e) { toast(String(e instanceof Error ? e.message : e), "error"); }
    pick.value = "";
  });
  const el = field(title, h("div", { class: "row" }, input, pick), undefined);
  el.append(h("label", {}, saveBox, t("keyfield.saveAs"), label));
  return {
    el,
    input,
    save: async () => {
      if (!saveBox.checked || !input.value.trim()) return;
      fill();
      try { await cmd("save_key", { kind, label: label.value || defaultKeyLabel(projectName(), kind), value: input.value.trim() }); }
      catch (e) { toast(t("keyfield.error", { error: e instanceof Error ? e.message : String(e) }), "error"); }
    },
  };
}
