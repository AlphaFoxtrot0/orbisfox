// OrbisFox panel: boot, sign-in, first-run setup and layout.
import { S, on, connect, setReauth } from "./store.js";
import { h, icon, api, act, setToken, getToken, frame, menu, toast, confirmBox } from "./util.js";
import { mountConsole } from "./console.js";
import { mountTop } from "./top.js";
import { mountDock } from "./panels.js";
import { openMap } from "./map.js";

let LOCAL = false;
let dock = null;

window.orbis = {
  openPanel: (id) => (id === "map" ? openMap() : dock && dock.open(id)),
  token: () => getToken(),
  get local() { return LOCAL; },
};

async function getLocalToken() {
  const r = await fetch("/api/token");
  if (!r.ok) throw new Error("no token");
  setToken((await r.json()).token);
}

async function tokenWorks() {
  if (!getToken()) return false;
  try { await api("settings"); return true; } catch { return false; }
}

function loginGate() {
  return new Promise((resolve) => {
    const pw = h("input", { class: "input", type: "password", placeholder: "Panel password", autocomplete: "current-password" });
    const err = h("div", { style: { color: "var(--ember)", minHeight: "22px", textAlign: "center" } });
    const go = async () => {
      try {
        const r = await fetch("/api/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ password: pw.value }) });
        const d = await r.json();
        if (!r.ok) throw new Error(d.error || "Sign-in failed");
        setToken(d.token);
        gate.remove();
        resolve();
      } catch (e) { err.textContent = e.message; pw.select(); }
    };
    pw.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
    const gate = h("div", { class: "gate" }, frame("box",
      h("img", { src: "img/mark-256.png", alt: "" }),
      h("h1", null, "Orbis", h("span", { style: { color: "var(--fox)" } }, "Fox")),
      h("p", null, "Sign in to manage this Hytale server."),
      pw, err,
      h("button", { class: "btn primary", onclick: go }, "Sign in")));
    document.body.append(gate);
    setTimeout(() => pw.focus(), 50);
  });
}

function setupGate() {
  if (document.querySelector(".gate")) return;
  const dir = h("input", { class: "input", placeholder: "C:\\Hytale\\Server", value: (S.settings && S.settings.server_dir) || "" });
  const msg = h("div", { class: "muted", style: { minHeight: "44px", fontSize: "16px", lineHeight: "22px" } });
  const save = async () => {
    const r = await act("settings", { changes: { server_dir: dir.value.trim() } });
    if (!r) return;
    const s = await api("settings");
    const probs = s.layout.problems || [];
    if (probs.length) { msg.style.color = "var(--amber-text)"; msg.textContent = probs.join(" "); return; }
    const java = s.java || {};
    gate.remove();
    toast("Server folder set", java.path && java.version >= 25 ? `Using Java ${java.version}. Press Start when you're ready.` : "Java 25 was not found yet: see Settings.", java.version >= 25 ? "info" : "warn", 9000);
    window.orbis.openPanel("settings");
  };
  dir.addEventListener("keydown", (e) => { if (e.key === "Enter") save(); });
  const gate = h("div", { class: "gate" }, frame("box",
    h("img", { src: "img/mark-256.png", alt: "" }),
    h("h1", null, "Welcome to Orbis", h("span", { style: { color: "var(--fox)" } }, "Fox")),
    h("p", null, "Where is your Hytale server? Pick the folder that holds HytaleServer.jar (the Server folder of the download)."),
    h("div", { class: "row" }, dir, LOCAL ? h("button", { class: "btn", onclick: async () => { const r = await act("pick-folder", { start: dir.value }); if (r && r.path) dir.value = r.path; } }, "Browse") : null),
    msg,
    h("div", { class: "note warn" }, icon("info"), h("div", null, "OrbisFox runs the server itself from now on. Close the old server window (start.bat) before you press Start.")),
    h("button", { class: "btn primary", onclick: save }, "Use this folder"),
    h("button", { class: "btn ghost sm", onclick: () => gate.remove() }, "Later")));
  document.body.append(gate);
  setTimeout(() => dir.focus(), 50);
}

function appMenu(anchor) {
  menu(anchor, [
    { label: "Open server folder", icon: "folder", onclick: () => act("open", { which: "server" }) },
    { label: "Open logs folder", icon: "terminal", onclick: () => act("open", { which: "logs" }) },
    { label: "Open backups folder", icon: "archive", onclick: () => act("open", { which: "backups" }) },
    "-",
    { label: "Settings", icon: "gear", onclick: () => window.orbis.openPanel("settings") },
    { label: "Help and source code", icon: "external", onclick: () => window.open("https://github.com/AlphaFoxtrot0/orbisfox", "_blank", "noopener") },
    "-",
    { label: "Quit OrbisFox", icon: "power", danger: true, onclick: async () => {
      const alive = ["starting", "running", "stopping"].includes(S.server.state);
      if (await confirmBox("Quit OrbisFox?", alive ? "The Hytale server is stopped first (players are disconnected), then OrbisFox closes. Closing this window instead keeps both running." : "OrbisFox closes. Closing this window instead keeps it running in the background.", "Quit", true)) {
        await act("app/quit", {});
        document.body.replaceChildren(h("div", { class: "gate" }, frame("box", h("img", { src: "img/mark-256.png", alt: "" }), h("h1", null, "OrbisFox closed"), h("p", null, "You can close this window."))));
      }
    } },
  ]);
}

async function boot() {
  let hello = { local: false };
  try { hello = await (await fetch("/api/hello")).json(); } catch { /* offline */ }
  LOCAL = !!hello.local;
  if (LOCAL) {
    try { await getLocalToken(); } catch { /* fall back to password */ }
    setReauth(getLocalToken);
  }
  if (!(await tokenWorks())) await loginGate();

  const top = h("header", { class: "top" });
  const consoleFrame = frame("console");
  consoleFrame.setAttribute("role", "main");
  const dockFrame = frame("dock");
  dockFrame.setAttribute("aria-label", "Panel");
  const rail = h("nav", { class: "rail", "aria-label": "Panels" });
  const app = h("div", { class: "app" }, top, consoleFrame, dockFrame, rail);
  document.getElementById("root").replaceChildren(app, h("div", { id: "toasts", class: "toasts" }));

  mountConsole(consoleFrame.querySelector(".frame-in"));
  mountTop(top, (id) => window.orbis.openPanel(id));
  top.append(h("button", { class: "btn ghost icon", "aria-label": "Menu", onclick: (e) => appMenu(e.currentTarget) }, icon("more")));
  dock = mountDock(dockFrame, rail, openMap);

  let firstSnap = true;
  on("snapshot", () => {
    document.title = S.server.name ? `${S.server.name} · OrbisFox` : "OrbisFox";
    if (firstSnap && (!S.settings.server_dir || (S.server.problems || []).length)) setupGate();
    // Deep links: #map, #panel=perf
    if (firstSnap && location.hash) {
      const m = /^#(map|panel=([a-z]+))$/.exec(location.hash);
      if (m) setTimeout(() => window.orbis.openPanel(m[2] || "map"), 300);
    }
    firstSnap = false;
  });
  on("server", () => { document.title = S.server.name ? `${S.server.name} · OrbisFox` : "OrbisFox"; });
  connect();
}

boot();
