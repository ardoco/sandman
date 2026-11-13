type Child = Node | string | null | undefined | false;

export function h<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  props: Record<string, unknown> = {},
  ...children: Child[]
): HTMLElementTagNameMap[K] {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v as EventListener);
    else if (k === "class") el.className = String(v);
    else if (typeof v === "boolean" || k === "value" || k === "checked" || k === "disabled") (el as any)[k] = v;
    else if (v !== undefined && v !== null) el.setAttribute(k, String(v));
  }
  el.append(...children.filter((c): c is Node | string => !!c).map((c) => (typeof c === "string" ? document.createTextNode(c) : c)));
  return el;
}

export function clear(el: Element, ...children: Child[]): void {
  el.replaceChildren(...children.filter((c): c is Node | string => !!c));
}

export function table(headers: string[], rows: Child[][], onRow?: (i: number) => void): HTMLTableElement {
  return h(
    "table",
    {},
    h("thead", {}, h("tr", {}, ...headers.map((t) => h("th", {}, t)))),
    h(
      "tbody",
      {},
      ...rows.map((r, i) => h("tr", onRow ? { class: "click", onclick: () => onRow(i) } : {}, ...r.map((c) => h("td", {}, c)))),
    ),
  );
}

export function select(options: (string | [string, string])[], value = ""): HTMLSelectElement {
  return h("select", { value }, ...options.map((o) => {
    const [v, label] = typeof o === "string" ? [o, o || "(any)"] : o;
    return h("option", { value: v }, label);
  }));
}

export function field(label: string, input: HTMLElement, hint?: string): HTMLLabelElement {
  return h("label", { class: "field" }, h("span", {}, label), input, hint ? h("small", {}, hint) : null);
}

export function toast(message: string, kind: "info" | "error" = "info"): void {
  const t = h("div", { class: `toast ${kind}` }, message);
  document.body.append(t);
  setTimeout(() => t.remove(), 6000);
}

export function fmt(v: unknown): string {
  return v === null || v === undefined ? "" : typeof v === "string" ? v : JSON.stringify(v);
}

export function showError(root: Element, e: unknown): void {
  root.append(h("p", { class: "error" }, e instanceof Error ? e.message : String(e)));
}

export function jsonView(v: unknown): HTMLPreElement {
  return h("pre", {}, JSON.stringify(v, null, 2));
}

export function autoRefresh(load: () => Promise<boolean>, ms = 3000): (() => void) & { kick: () => void } {
  let timer: ReturnType<typeof setTimeout> | undefined;
  let stopped = false;
  const tick = async () => {
    clearTimeout(timer);
    if (stopped || document.hidden) return;
    const again = await load().catch(() => false);
    if (again && !stopped) timer = setTimeout(tick, ms);
  };
  const onVisible = () => { if (!document.hidden) void tick(); };
  document.addEventListener("visibilitychange", onVisible);
  void tick();
  return Object.assign(() => { stopped = true; clearTimeout(timer); document.removeEventListener("visibilitychange", onVisible); }, { kick: () => void tick() });
}
