// Small helpers shared by every part of the panel: DOM building, icons, API calls, toasts,
// dialogs, menus and formatting.

export function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  if (attrs) {
    for (const [k, v] of Object.entries(attrs)) {
      if (v == null || v === false) continue;
      if (k === "class") el.className = v;
      else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
      else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
      else if (k === "html") el.innerHTML = v;
      else if (k in el && typeof v !== "string") el[k] = v;
      else el.setAttribute(k, v === true ? "" : v);
    }
  }
  append(el, kids);
  return el;
}

function append(el, kids) {
  for (const k of kids) {
    if (k == null || k === false) continue;
    if (Array.isArray(k)) append(el, k);
    else el.append(k instanceof Node ? k : document.createTextNode(String(k)));
  }
}

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

export function clear(el, ...kids) { el.replaceChildren(); append(el, kids); return el; }

// --------------------------------------------------------------------------- icons
// Simple line icons drawn for OrbisFox on a 24-unit grid.
const ICONS = {
  play: '<path d="M7 4.5v15l12-7.5z"/>',
  stop: '<rect x="6" y="6" width="12" height="12" rx="1.5"/>',
  restart: '<path d="M20 12a8 8 0 1 1-2.4-5.7"/><path d="M20 4v5h-5"/>',
  power: '<path d="M12 3v9"/><path d="M6.3 6.8a8 8 0 1 0 11.4 0"/>',
  terminal: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 9l3 3-3 3"/><path d="M12.5 15H17"/>',
  users: '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20c.6-3.6 3.3-5.5 6.5-5.5s5.9 1.9 6.5 5.5"/><path d="M16 4.8a3.5 3.5 0 0 1 0 6.4"/><path d="M18 14.8c2 .7 3.2 2.4 3.6 5.2"/>',
  shield: '<path d="M12 3l8 3v6c0 4.5-3.4 8-8 9-4.6-1-8-4.5-8-9V6z"/><path d="M8.5 12l2.5 2.5 4.5-5"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2.5M12 19.5V22M2 12h2.5M19.5 12H22M4.9 4.9l1.8 1.8M17.3 17.3l1.8 1.8M4.9 19.1l1.8-1.8M17.3 6.7l1.8-1.8"/>',
  moon: '<path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z"/>',
  archive: '<rect x="3" y="4" width="18" height="5" rx="1"/><path d="M5 9v10a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V9"/><path d="M10 13h4"/>',
  download: '<path d="M12 4v11"/><path d="M7 10l5 5 5-5"/><path d="M4 20h16"/>',
  upload: '<path d="M12 20V9"/><path d="M7 14l5-5 5 5"/><path d="M4 4h16"/>',
  trophy: '<path d="M8 4h8v5a4 4 0 0 1-8 0z"/><path d="M8 6H4.5a3 3 0 0 0 3.5 4M16 6h3.5a3 3 0 0 1-3.5 4"/><path d="M12 13v4M8 20h8M9.5 17h5"/>',
  pulse: '<path d="M3 12h4l2.5-6 4 12 2.5-6H21"/>',
  map: '<path d="M3 6.5l6-2.5 6 2.5 6-2.5v13.5l-6 2.5-6-2.5-6 2.5z"/><path d="M9 4v13.5M15 6.5V20"/>',
  gear: '<circle cx="12" cy="12" r="3"/><path d="M12 2.5l1.6 2.6 3-.7.7 3 2.6 1.6-1.5 2.9 1.5 2.9-2.6 1.6-.7 3-3-.7L12 21.5l-1.6-2.6-3 .7-.7-3-2.6-1.6L5.6 12 4.1 9.1l2.6-1.6.7-3 3 .7z"/>',
  search: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="M15.5 15.5L21 21"/>',
  send: '<path d="M4 12l16-8-6 16-2.5-6.5z"/><path d="M11.5 13.5L20 4"/>',
  copy: '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V5a1 1 0 0 0-1-1H5a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h3"/>',
  trash: '<path d="M4 7h16"/><path d="M9 7V4h6v3"/><path d="M6 7l1 13h10l1-13"/>',
  x: '<path d="M6 6l12 12M18 6L6 18"/>',
  check: '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  alert: '<path d="M12 3l9.5 17h-19z"/><path d="M12 10v4.5"/><circle cx="12" cy="17.3" r=".6" fill="currentColor"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6"/><circle cx="12" cy="7.8" r=".6" fill="currentColor"/>',
  kick: '<path d="M14 4h5a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1h-5"/><path d="M10 8l-4 4 4 4"/><path d="M6 12h10"/>',
  ban: '<circle cx="12" cy="12" r="8.5"/><path d="M6 6l12 12"/>',
  crown: '<path d="M3.5 8l4.5 4 4-7 4 7 4.5-4-2 11h-13z"/>',
  message: '<path d="M4 5h16v11H9l-5 4z"/>',
  more: '<circle cx="5.5" cy="12" r="1.2" fill="currentColor"/><circle cx="12" cy="12" r="1.2" fill="currentColor"/><circle cx="18.5" cy="12" r="1.2" fill="currentColor"/>',
  folder: '<path d="M3 6.5a1.5 1.5 0 0 1 1.5-1.5H9l2 2.5h8.5A1.5 1.5 0 0 1 21 9v9.5a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 18.5z"/>',
  pause: '<path d="M8 5v14M16 5v14"/>',
  down: '<path d="M12 5v14M6 13l6 6 6-6"/>',
  refresh: '<path d="M20 11a8 8 0 0 0-14.3-4.5L4 8"/><path d="M4 3v5h5"/><path d="M4 13a8 8 0 0 0 14.3 4.5L20 16"/><path d="M20 21v-5h-5"/>',
  cpu: '<rect x="6" y="6" width="12" height="12" rx="1.5"/><rect x="9.5" y="9.5" width="5" height="5"/><path d="M9 2.5V6M15 2.5V6M9 18v3.5M15 18v3.5M2.5 9H6M2.5 15H6M18 9h3.5M18 15h3.5"/>',
  memory: '<rect x="3" y="7" width="18" height="10" rx="1.5"/><path d="M7 7v10M11 7v10M15 7v10M5 20v-3M19 20v-3"/>',
  gpu: '<rect x="2.5" y="6" width="19" height="11" rx="1.5"/><circle cx="9" cy="11.5" r="2.8"/><path d="M15 9.5h3.5M15 13.5h3.5M5 17v2.5M10 17v2.5"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3.5 2"/>',
  cloud: '<path d="M7 18a4.5 4.5 0 0 1-.6-9 6 6 0 0 1 11.4 1.5A3.8 3.8 0 0 1 17.5 18z"/>',
  rain: '<path d="M7 15a4.5 4.5 0 0 1-.6-9 6 6 0 0 1 11.4 1.5A3.8 3.8 0 0 1 17.5 15z"/><path d="M8 18l-1 2.5M12 18l-1 2.5M16 18l-1 2.5"/>',
  storm: '<path d="M7 14a4.5 4.5 0 0 1-.6-9 6 6 0 0 1 11.4 1.5A3.8 3.8 0 0 1 17.5 14z"/><path d="M12.5 14l-2.5 4h4l-2.5 4"/>',
  snow: '<path d="M12 3v18M4.2 7.5l15.6 9M4.2 16.5l15.6-9"/>',
  save: '<path d="M5 3.5h11l3.5 3.5v12a1.5 1.5 0 0 1-1.5 1.5H6A1.5 1.5 0 0 1 4.5 19V5A1.5 1.5 0 0 1 5 3.5z"/><path d="M8 3.5V8h7V3.5M8 20.5V14h8v6.5"/>',
  megaphone: '<path d="M3.5 10v4h3l8 4.5V5.5l-8 4.5z"/><path d="M18 9a4 4 0 0 1 0 6"/><path d="M7 14l1.5 5.5h2.5L10 15"/>',
  skull: '<path d="M12 3a7.5 7.5 0 0 0-4.5 13.5V20h9v-3.5A7.5 7.5 0 0 0 12 3z"/><circle cx="9" cy="11" r="1.6"/><circle cx="15" cy="11" r="1.6"/><path d="M10.5 20v-2M13.5 20v-2"/>',
  sword: '<path d="M20 4l-1 5L9.5 18.5 5.5 14.5 15 5z"/><path d="M4 15l5 5M3 21l3-3M7.5 12.5l4 4"/>',
  boot: '<path d="M7 3h6v8l6 3.5a2 2 0 0 1 1 1.7V19H5V3z"/><path d="M5 15h15"/>',
  heart: '<path d="M12 20s-8-4.7-8-10.5A4.5 4.5 0 0 1 12 7a4.5 4.5 0 0 1 8 2.5C20 15.3 12 20 12 20z"/>',
  pick: '<path d="M4 9c4-5 12-5 16 0"/><path d="M12 7.5L6 20"/>',
  cube: '<path d="M12 2.5l8.5 4.8v9.4L12 21.5l-8.5-4.8V7.3z"/><path d="M12 12l8.5-4.7M12 12L3.5 7.3M12 12v9.5"/>',
  target: '<circle cx="12" cy="12" r="8.5"/><circle cx="12" cy="12" r="4.5"/><circle cx="12" cy="12" r="1" fill="currentColor"/>',
  layers: '<path d="M12 3l9 5-9 5-9-5z"/><path d="M3 13l9 5 9-5"/>',
  key: '<circle cx="8" cy="15" r="4.5"/><path d="M11.2 11.8L20 3M16 7l3 3M14 9l2 2"/>',
  wifi: '<path d="M2.5 9a14 14 0 0 1 19 0"/><path d="M5.8 12.5a9 9 0 0 1 12.4 0"/><path d="M9 16a4.3 4.3 0 0 1 6 0"/><circle cx="12" cy="19.2" r=".8" fill="currentColor"/>',
  external: '<path d="M14 4h6v6"/><path d="M20 4l-9 9"/><path d="M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>',
  locate: '<circle cx="12" cy="12" r="7"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3"/><circle cx="12" cy="12" r="1.5" fill="currentColor"/>',
  zoomin: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="M15.5 15.5L21 21M10.5 7.5v6M7.5 10.5h6"/>',
  zoomout: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="M15.5 15.5L21 21M7.5 10.5h6"/>',
  dock: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M15 4v16"/>',
  plug: '<path d="M9 3v5M15 3v5"/><path d="M6 8h12v3a6 6 0 0 1-12 0z"/><path d="M12 17v4"/>',
  wrench: '<path d="M15 4a5 5 0 0 0-5.5 6.5L3.5 16.5 7.5 20.5l6-6A5 5 0 0 0 20 9l-3 3-3-1-1-3z"/>',
  gamepad: '<path d="M7 7h10a4.5 4.5 0 0 1 4.4 5.5l-1 4.4a2.3 2.3 0 0 1-4 1L14.5 16h-5l-1.9 1.9a2.3 2.3 0 0 1-4-1l-1-4.4A4.5 4.5 0 0 1 7 7z"/><path d="M8 10v4M6 12h4"/><circle cx="16" cy="11" r=".7" fill="currentColor"/><circle cx="17.5" cy="13" r=".7" fill="currentColor"/>',
  gift: '<rect x="3.5" y="8" width="17" height="4" rx="1"/><path d="M5 12v8h14v-8M12 8v12"/><path d="M12 8c-1.5-4-6-4-5-1 .5 1.2 3 1 5 1zM12 8c1.5-4 6-4 5-1-.5 1.2-3 1-5 1z"/>',
  book: '<path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H20v15H6.5A2.5 2.5 0 0 0 4 20.5z"/><path d="M4 20.5A2.5 2.5 0 0 0 6.5 23H20v-5"/><path d="M9 7.5h7M9 11h5"/>',
  hourglass: '<path d="M6 3h12M6 21h12M7 3c0 5 10 5 10 9s-10 4-10 9M17 3c0 5-10 5-10 9s10 4 10 9"/>',
};

export function icon(name, cls = "") {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("class", `ico ${cls}`);
  svg.setAttribute("aria-hidden", "true");
  svg.innerHTML = ICONS[name] || ICONS.info;
  return svg;
}

// --------------------------------------------------------------------------- API
let TOKEN = null;
try { TOKEN = sessionStorage.getItem("orbisfox-token"); } catch { /* storage off */ }

export function setToken(t) {
  TOKEN = t;
  try { sessionStorage.setItem("orbisfox-token", t); } catch { /* storage off */ }
}
export const getToken = () => TOKEN;

export async function api(path, body, { method } = {}) {
  const opts = { method: method || (body !== undefined ? "POST" : "GET"), headers: {} };
  if (TOKEN) opts.headers["X-OrbisFox-Token"] = TOKEN;
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(`/api/${path}`, opts);
  let data = null;
  try { data = await res.json(); } catch { /* empty */ }
  if (!res.ok) {
    const err = new Error((data && data.error) || `Request failed (${res.status})`);
    err.status = res.status;
    throw err;
  }
  return data;
}

// Runs an API call and reports failures as a toast. Returns the result or undefined.
export async function act(path, body, okMessage) {
  try {
    const r = await api(path, body);
    if (okMessage) toast(okMessage, "", "info");
    return r ?? {};
  } catch (e) {
    toast("That didn't work", e.message, "bad");
    return undefined;
  }
}

// --------------------------------------------------------------------------- toasts
export function toast(title, body = "", level = "info", ms = 5000) {
  const box = $("#toasts");
  const ico = level === "bad" ? "alert" : level === "warn" ? "alert" : "info";
  const el = h("div", { class: `toast ${level}`, role: level === "bad" ? "alert" : "status" },
    icon(ico),
    h("div", { class: "grow" }, h("div", { class: "t" }, title), body ? h("div", { class: "b" }, body) : null),
    h("button", { class: "btn ghost icon xs", "aria-label": "Dismiss", onclick: () => el.remove() }, icon("x", "sm")));
  box.append(el);
  if (ms) setTimeout(() => el.remove(), ms);
}

// --------------------------------------------------------------------------- dialogs
export function frame(cls, ...kids) {
  return h("div", { class: `frame ${cls || ""}` }, h("div", { class: "frame-in" }, ...kids));
}

export function modal({ title, body, actions = [], wide = false, onClose }) {
  const back = h("div", { class: "modal-back" });
  const close = () => { back.remove(); document.removeEventListener("keydown", onKey); onClose && onClose(); };
  const onKey = (e) => { if (e.key === "Escape") close(); };
  document.addEventListener("keydown", onKey);
  back.addEventListener("mousedown", (e) => { if (e.target === back) close(); });
  const foot = actions.length ? h("div", { class: "modal-foot" }, actions.map((a) =>
    h("button", { class: `btn ${a.cls || ""}`, onclick: async () => { const r = a.onclick ? await a.onclick() : true; if (r !== false) close(); } }, a.icon ? icon(a.icon) : null, a.label))) : null;
  const m = frame(`modal ${wide ? "wide" : ""}`,
    h("div", { class: "frame-head" }, h("h2", { class: "frame-title grow" }, title),
      h("button", { class: "btn ghost icon sm", "aria-label": "Close", onclick: close }, icon("x"))),
    wide ? body : h("div", { class: "modal-body" }, body),
    foot);
  m.setAttribute("role", "dialog");
  m.setAttribute("aria-modal", "true");
  back.append(m);
  document.body.append(back);
  setTimeout(() => { const f = back.querySelector("input, select, textarea, .btn.primary"); f && f.focus(); }, 30);
  return { close, el: m };
}

export function confirmBox(title, text, okLabel = "Continue", danger = false) {
  return new Promise((resolve) => {
    let done = false;
    modal({
      title, body: h("p", { style: { margin: 0, fontSize: "18px", lineHeight: "26px" } }, text),
      actions: [
        { label: "Cancel", onclick: () => { done = true; resolve(false); } },
        { label: okLabel, cls: danger ? "danger" : "primary", onclick: () => { done = true; resolve(true); } },
      ],
      onClose: () => { if (!done) resolve(false); },
    });
  });
}

export function promptBox(title, { label, value = "", placeholder = "", okLabel = "OK", help, multiline } = {}) {
  return new Promise((resolve) => {
    let done = false;
    const inp = multiline
      ? h("textarea", { class: "input", rows: 3, placeholder, value })
      : h("input", { class: "input", placeholder, value });
    const submit = () => { done = true; resolve(inp.value.trim()); };
    inp.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit(); m.close(); } });
    const m = modal({
      title,
      body: h("div", { class: "field" }, label ? h("span", { class: "label" }, label) : null, inp, help ? h("span", { class: "help" }, help) : null),
      actions: [{ label: "Cancel" }, { label: okLabel, cls: "primary", onclick: submit }],
      onClose: () => { if (!done) resolve(null); },
    });
  });
}

// --------------------------------------------------------------------------- menus
let openMenu = null;
export function menu(anchor, items) {
  closeMenu();
  const el = h("div", { class: "menu", role: "menu" }, items.map((it) => it === "-" ? h("div", { class: "sep" }) :
    h("button", { role: "menuitem", class: it.danger ? "danger" : "", onclick: () => { closeMenu(); it.onclick(); } },
      it.icon ? icon(it.icon) : null, it.label, it.hint ? h("span", { class: "hint" }, it.hint) : null)));
  document.body.append(el);
  const r = anchor.getBoundingClientRect();
  const mw = el.offsetWidth, mh = el.offsetHeight;
  let left = Math.min(r.right - mw, window.innerWidth - mw - 8);
  left = Math.max(8, left);
  let top = r.bottom + 6;
  if (top + mh > window.innerHeight - 8) top = Math.max(8, r.top - mh - 6);
  el.style.left = `${left}px`;
  el.style.top = `${top}px`;
  openMenu = el;
  setTimeout(() => { el.querySelector("button")?.focus(); });
  return el;
}
export function closeMenu() { if (openMenu) { openMenu.remove(); openMenu = null; } }
document.addEventListener("mousedown", (e) => { if (openMenu && !openMenu.contains(e.target)) closeMenu(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeMenu(); });

// --------------------------------------------------------------------------- formatting
export function bytes(n, digits = 1) {
  if (n == null || isNaN(n)) return "–";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(i === 0 ? 0 : digits)} ${u[i]}`;
}

export function ago(ts) {
  if (!ts) return "never";
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 45) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) { const hrs = Math.floor(s / 3600); return `${hrs} h ${Math.round((s % 3600) / 60)} min ago`; }
  const d = Math.floor(s / 86400);
  return d === 1 ? "yesterday" : `${d} days ago`;
}

export function duration(s) {
  if (s == null) return "–";
  s = Math.max(0, Math.round(s));
  const d = Math.floor(s / 86400), hr = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  if (d) return `${d}d ${hr}h`;
  if (hr) return `${hr}h ${m}m`;
  if (m) return `${m}m ${s % 60}s`;
  return `${s}s`;
}

export function clock(ts) {
  const d = new Date(ts * 1000);
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
}

export function dateTime(ts) {
  const d = new Date(ts * 1000);
  return d.toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

export function distance(blocks) {
  if (blocks == null) return "–";
  if (blocks >= 1000) return `${(blocks / 1000).toFixed(blocks >= 100000 ? 0 : 1)} km`;
  return `${Math.round(blocks)} m`;
}

export function num(n) {
  if (n == null) return "–";
  return Number(n).toLocaleString();
}

export function initials(name) {
  const parts = String(name || "?").replace(/[^A-Za-z0-9 _]/g, "").split(/[ _]+/).filter(Boolean);
  if (!parts.length) return "?";
  return (parts.length > 1 ? parts[0][0] + parts[1][0] : parts[0].slice(0, 2)).toUpperCase();
}

// Sparkline / area chart as inline SVG.
export function spark(values, { w = 84, h: ht = 18, max = 100, color = "var(--cyan)", fill = true } = {}) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", `0 0 ${w} ${ht}`);
  svg.setAttribute("preserveAspectRatio", "none");
  svg.setAttribute("class", "spark");
  const vals = values.filter((v) => v != null);
  if (vals.length < 2) return svg;
  const m = Math.max(max || 0, ...vals) || 1;
  const pts = vals.map((v, i) => [(i / (vals.length - 1)) * w, ht - 1 - (v / m) * (ht - 2)]);
  const d = pts.map((p, i) => `${i ? "L" : "M"}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join("");
  svg.innerHTML = (fill ? `<path d="${d}L${w},${ht}L0,${ht}Z" fill="${color}" opacity="0.15"/>` : "") +
    `<path d="${d}" fill="none" stroke="${color}" stroke-width="1.5" vector-effect="non-scaling-stroke"/>`;
  return svg;
}

export function debounce(fn, ms) {
  let t;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}
