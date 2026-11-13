import { t } from "../i18n";
import { cmd, SecretsState } from "../api";
import { h, showError, toast } from "./dom";

export function secretsBox(onChange?: (present: boolean) => void): { el: HTMLElement; check: (checkout: string) => Promise<boolean> } {
  let checkout = "";
  const status = h("span", {});
  const btn = h("button", { type: "button", class: "secondary" }, t("secrets.generate"));
  const msg = h("div", {});
  const el = h("div", { class: "field" }, h("div", { class: "row" }, status, btn),
    h("small", {}, t("secrets.hint")), msg);
  el.hidden = true;
  async function check(path: string): Promise<boolean> {
    checkout = path;
    try {
      const s = await cmd<SecretsState>("secrets_state", { checkout });
      status.textContent = t(s.present ? "secrets.present" : "secrets.missing");
      btn.disabled = s.present;
      el.hidden = false;
      onChange?.(s.present);
      return s.present;
    } catch {
      el.hidden = true;
      return false;
    }
  }
  btn.addEventListener("click", async () => {
    msg.replaceChildren();
    btn.disabled = true;
    try {
      await cmd("generate_secrets", { checkout });
      toast(t("secrets.done"));
    } catch (e) { showError(msg, e); }
    await check(checkout);
  });
  return { el, check };
}
