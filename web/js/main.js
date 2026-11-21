// OrbisFox panel: boot, sign-in, first-run setup and layout.
import { S, on, connect, setReauth } from "./store.js";
import { h, icon, api, act, setToken, getToken, frame, menu, toast, confirmBox } from "./util.js";
import { mountConsole } from "./console.js";
import { mountTop } from "./top.js";
import { mountDock } from "./panels.js";
import { openMap } from "./map.js";
import { pickServerFolder } from "./folderpicker.js";

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
  const msg = h("div", { role: "status", style: { minHeight: "44px", fontSize: "16px", lineHeight: "22px", color: "var(--ink-muted)" } });
  const say = (text, bad) => { msg.style.color = bad ? "var(--amber-text)" : "var(--ink-muted)"; msg.textContent = text; };
  const useBtn = h("button", { class: "btn primary" }, "Use this folder");
  const save = async () => {
    // "Copy as path" in Explorer wraps the path in quotes; a file path (HytaleServer.jar) is fine too.
    let path = dir.value.trim().replace(/^"+|"+$/g, "").trim();
    if (/hytaleserver\.jar$/i.test(path)) path = path.replace(/[\\/][^\\/]*$/, "");
    if (!path) { say("Type or paste the folder path, or press Browse.", true); return; }
    dir.value = path;
    useBtn.disabled = true;
    useBtn.textContent = "Checking...";
    try {
      const r = await api("settings", { changes: { server_dir: path } });
      const s = await api("settings");
      const probs = (s.layout && s.layout.problems) || [];
      if (probs.length) { say(probs.join(" "), true); return; }
      const java = s.java || {};
      gate.remove();
      const found = (r.imported || []).length ? ` Imported from your server: ${r.imported.slice(0, 4).join("; ")}.` : "";
      toast("Server folder set", (java.path && java.version >= 25 ? `Using Java ${java.version}.` : "Java 25 was not found yet: see Settings.") + found + " Close the old start.bat window, then press Start.",
        java.version >= 25 ? "info" : "warn", 15000);
      window.orbis.openPanel("settings");
    } catch (e) {
      say(`That didn't work: ${e.message}`, true);
    } finally {
      useBtn.disabled = false;
      useBtn.textContent = "Use this folder";
    }
  };
  useBtn.onclick = save;
  dir.addEventListener("keydown", (e) => { if (e.key === "Enter") save(); });
  const browse = h("button", { class: "btn", onclick: async () => {
    const p = await pickServerFolder(dir.value.trim().replace(/^"+|"+$/g, ""));
    if (p) { dir.value = p; save(); }
  } }, icon("folder"), "Browse");
  const gate = h("div", { class: "gate" }, frame("box",
    h("img", { src: "img/mark-256.png", alt: "" }),
    h("h1", null, "Welcome to Orbis", h("span", { style: { color: "var(--fox)" } }, "Fox")),
    h("p", null, "Where is your Hytale server? Pick the folder that holds HytaleServer.jar (the Server folder of the download)."),
    h("div", { class: "row" }, dir, browse),
    msg,
    h("div", { class: "note warn" }, icon("info"), h("div", null, "OrbisFox runs the server itself from now on. Close the old server window (start.bat) before you press Start.")),
    useBtn,
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
