// The console: live server output with filters and search, and the command line.
import { S, on, isRunning } from "./store.js";
import { h, icon, api, act, toast, menu, clock, debounce, promptBox } from "./util.js";

const MAX_DOM = 4000;
const LOG_RE = /^\[(\d{4})\/(\d{2})\/(\d{2}) (\d{2}):(\d{2}):(\d{2}) +([A-Z]{1,7})\] *(?:\[([^\]\n]{1,80})\] ?)?(.*)$/;

const FILTERS = [
  { id: "all", label: "All" },
  { id: "chat", label: "Chat", kinds: ["chat", "cmd"], test: (ln) => ln.kind === "chat" || (ln.kind === "cmd" && /^(say|broadcast|notify|eventtitle) /i.test(ln.text)) },
  { id: "players", label: "Players", kinds: ["join", "leave", "death", "chat"] },
  { id: "problems", label: "Problems", kinds: ["warn", "error"] },
  { id: "orbisfox", label: "OrbisFox", kinds: ["app", "cmd"] },
];

const state = {
  filter: localGet("con-filter", "all"),
  search: "",
  times: localGet("con-times", "1") === "1",
  wrap: localGet("con-wrap", "1") === "1",
  raw: localGet("con-raw", "0") === "1",
  stick: true,
  unseen: 0,
  history: JSON.parse(localGet("con-history", "[]")),
  hpos: -1,
  mode: "cmd",
};

function localGet(k, d) { try { return localStorage.getItem(`orbisfox-${k}`) ?? d; } catch { return d; } }
function localSet(k, v) { try { localStorage.setItem(`orbisfox-${k}`, v); } catch { /* storage off */ } }

let out, newBtn, input, suggestBox, counts = {};

function visible(ln) {
  const f = FILTERS.find((x) => x.id === state.filter);
  if (f && f.id !== "all") {
    if (f.test ? !f.test(ln) : !f.kinds.includes(ln.kind)) return false;
  }
  if (state.search && !ln.text.toLowerCase().includes(state.search)) return false;
  return true;
}

function highlight(text) {
  if (!state.search) return document.createTextNode(text);
  const frag = document.createDocumentFragment();
  const low = text.toLowerCase();
  let i = 0;
  for (;;) {
    const j = low.indexOf(state.search, i);
    if (j < 0) break;
    frag.append(text.slice(i, j), h("mark", null, text.slice(j, j + state.search.length)));
    i = j + state.search.length;
  }
  frag.append(text.slice(i));
  return frag;
}

function render(ln) {
  let body = ln.text, logger = "", lvl = ln.lvl || "";
  if (!state.raw && (ln.kind !== "app" && ln.kind !== "cmd")) {
    const m = LOG_RE.exec(ln.text);
    if (m) { lvl = m[7]; logger = m[8] || ""; body = m[9] || ""; }
  }
  const m = h("span", { class: "m" });
  if (ln.kind === "cmd") {
    m.append(highlight(`> ${body}`));
  } else if (ln.kind === "chat") {
    const cm = /^\[(?:OrbisFox\|)?Chat\] (.+?): (.*)$/.exec(body);
    if (cm) m.append(h("span", { class: "who" }, cm[1]), ": ", highlight(cm[2]));
    else m.append(highlight(body));
  } else {
    if (logger && !state.raw) m.append(h("span", { class: "muted" }, `${logger.replace(/\|P$/, "")} `));
    if (lvl === "SEVERE" && !state.raw) m.append(h("b", null, "ERROR "));
    m.append(highlight(body));
  }
  return h("div", { class: `ln k-${ln.kind}`, "data-id": ln.id },
    h("span", { class: "t" }, clock(ln.t)), m);
}

function atBottom() { return out.scrollHeight - out.scrollTop - out.clientHeight < 40; }

let queue = [];
let raf = 0;
function enqueue(ln) {
  counts[ln.kind] = (counts[ln.kind] || 0) + 1;
  if (!visible(ln)) return;
  queue.push(ln);
  if (!raf) raf = requestAnimationFrame(flush);
}

function flush() {
  raf = 0;
  if (!queue.length) return;
  const stick = state.stick;
  const frag = document.createDocumentFragment();
  for (const ln of queue) frag.append(render(ln));
  const n = queue.length;
  queue = [];
  out.querySelector(".con-empty")?.remove();
  out.append(frag);
  while (out.childElementCount > MAX_DOM) out.firstElementChild.remove();
  if (stick) out.scrollTop = out.scrollHeight;
  else { state.unseen += n; updateNewBtn(); }
}

function rebuild() {
  out.replaceChildren();
  const lines = S.console.filter(visible).slice(-MAX_DOM);
  if (!lines.length) out.append(emptyState());
  const frag = document.createDocumentFragment();
  for (const ln of lines) frag.append(render(ln));
  out.append(frag);
  out.classList.toggle("no-time", !state.times);
  out.classList.toggle("nowrap", !state.wrap);
  out.scrollTop = out.scrollHeight;
  state.stick = true;
  state.unseen = 0;
  updateNewBtn();
}

function emptyState() {
  const msg = state.search || state.filter !== "all"
    ? "Nothing matches this filter yet."
    : S.server.state === "stopped" || S.server.state === "crashed"
      ? "The server is stopped. Press Start to bring it up."
      : "Waiting for the server to say something...";
  return h("div", { class: "con-empty" }, h("img", { src: "img/mark-256.png", alt: "" }), h("div", null, msg));
}

function updateNewBtn() {
  newBtn.classList.toggle("hidden", state.stick || state.unseen === 0);
  newBtn.querySelector(".lbl").textContent = `${state.unseen} new line${state.unseen === 1 ? "" : "s"}`;
}

// ------------------------------------------------------------------------- command line
function catalogFor(text) {
  const t = text.replace(/^\//, "").toLowerCase();
  const words = t.split(/\s+/);
  const opts = [];
  const players = [...new Set([...(S.players.online || []).map((p) => p.name), ...knownNames])];
  // Arguments: suggest player names where the command takes <player>.
  for (const c of S.catalog) {
    if (t.startsWith(c.cmd + " ") && /<player>|<target/.test(c.args)) {
      const rest = t.slice(c.cmd.length + 1);
      const parts = rest.split(/\s+/);
      const argIdx = c.args.split(/\s+/).findIndex((a) => a.includes("player"));
      if (parts.length - 1 === argIdx || (argIdx === -1 && parts.length === 1)) {
        const partial = parts[parts.length - 1];
        for (const p of players) {
          if (p.toLowerCase().startsWith(partial) && p.toLowerCase() !== partial) {
            const online = (S.players.online || []).some((o) => o.name === p);
            opts.push({ k: `${c.cmd} ${[...parts.slice(0, -1), p].join(" ")}`, d: online ? "online now" : "has played here", fill: true });
          }
        }
      }
      if (opts.length) return opts.slice(0, 8);
    }
  }
  if (!t) return [];
  for (const c of S.catalog) {
    if (c.cmd.startsWith(t) || (words.length === 1 && c.cmd.split(" ")[0].startsWith(words[0]) && c.cmd.startsWith(t))) {
      opts.push({ k: c.cmd, a: c.args, d: c.desc });
    }
  }
  return opts.slice(0, 10);
}

let knownNames = [], knownAt = 0;
function refreshKnown() {
  if (Date.now() - knownAt < 60000) return;
  knownAt = Date.now();
  api("stats").then((t) => { knownNames = t.players.map((p) => p.name).filter(Boolean); }).catch(() => {});
}

let sel = -1, opts = [];
function showSuggest() {
  if (state.mode !== "cmd") { hideSuggest(); return; }
  opts = catalogFor(input.value);
  if (!opts.length || (opts.length === 1 && opts[0].k === input.value.trim())) { hideSuggest(); return; }
  sel = Math.min(sel, opts.length - 1);
  suggestBox.replaceChildren(...opts.map((o, i) => h("div", {
    class: "opt", role: "option", "aria-selected": i === sel ? "true" : "false",
    onmousedown: (e) => { e.preventDefault(); accept(i); },
  }, h("span", { class: "k" }, o.k, o.a ? h("span", { class: "muted" }, ` ${o.a}`) : null), h("span", { class: "d" }, o.d))));
  suggestBox.classList.remove("hidden");
}
function hideSuggest() { suggestBox.classList.add("hidden"); sel = -1; }
function accept(i) {
  const o = opts[i];
  if (!o) return;
  input.value = o.k + (o.fill ? "" : (o.a ? " " : ""));
  input.focus();
  sel = -1;
  showSuggest();
}

async function submit() {
  const text = input.value.trim();
  if (!text) return;
  if (!isRunning() && S.server.state !== "starting") {
    toast("The server is not running", "Start it first, then send commands.", "warn");
    return;
  }
  let ok;
  if (state.mode === "cmd") ok = await act("command", { line: text });
  else ok = await act("say", { message: text, style: state.mode === "say" ? "chat" : state.mode, level: state.mode === "notify" ? "" : "" });
  if (ok === undefined) return;
  state.history = [text, ...state.history.filter((x) => x !== text)].slice(0, 100);
  localSet("con-history", JSON.stringify(state.history));
  state.hpos = -1;
  input.value = "";
  hideSuggest();
  state.stick = true;
  out.scrollTop = out.scrollHeight;
}

function onKey(e) {
  const open = !suggestBox.classList.contains("hidden");
  if (e.key === "Enter") {
    e.preventDefault();
    if (open && sel >= 0) { accept(sel); return; }
    submit();
  } else if (e.key === "Tab") {
    if (open) { e.preventDefault(); accept(Math.max(0, sel)); }
  } else if (e.key === "ArrowDown") {
    if (open) { e.preventDefault(); sel = (sel + 1) % opts.length; showSuggest(); }
    else if (state.hpos > -1) { e.preventDefault(); state.hpos--; input.value = state.hpos < 0 ? "" : state.history[state.hpos]; }
  } else if (e.key === "ArrowUp") {
    if (open && sel > 0) { e.preventDefault(); sel--; showSuggest(); }
    else if (open && sel === 0) { e.preventDefault(); sel = -1; showSuggest(); }
    else if (state.history.length) {
      e.preventDefault();
      state.hpos = Math.min(state.hpos + 1, state.history.length - 1);
      input.value = state.history[state.hpos];
      hideSuggest();
    }
  } else if (e.key === "Escape") {
    hideSuggest();
  }
}

const MODES = [
  { id: "cmd", label: "Command", ph: "Type a command, e.g. who or time set dawn   (Tab completes)" },
  { id: "say", label: "Say", ph: "Chat to everyone as the server" },
  { id: "notify", label: "Notice", ph: "On-screen notice for every player" },
  { id: "title", label: "Title", ph: "Big title across every player's screen" },
];

function setMode(m) {
  state.mode = m;
  const md = MODES.find((x) => x.id === m);
  input.placeholder = md.ph;
  modeBtn.querySelector(".lbl").textContent = md.label;
  input.classList.toggle("say", m !== "cmd");
  hideSuggest();
  input.focus();
}
let modeBtn;

// ------------------------------------------------------------------------- quick actions
async function timeMenu(anchor) {
  menu(anchor, [
    { label: "Dawn", icon: "sun", onclick: () => act("world/time", { preset: "dawn" }, "Time set to dawn") },
    { label: "Midday", icon: "sun", onclick: () => act("world/time", { preset: "midday" }, "Time set to midday") },
    { label: "Dusk", icon: "moon", onclick: () => act("world/time", { preset: "dusk" }, "Time set to dusk") },
    { label: "Midnight", icon: "moon", onclick: () => act("world/time", { preset: "midnight" }, "Time set to midnight") },
    "-",
    { label: "Pause / resume the day cycle", icon: "pause", onclick: () => act("world/time", { pause: true }) },
  ]);
}

async function weatherMenu(anchor) {
  menu(anchor, [
    { label: "Natural weather", icon: "sun", onclick: () => act("world/weather", { id: "reset" }, "Weather back to natural") },
    { label: "Rain (Zone 1)", icon: "rain", onclick: () => act("world/weather", { id: "Zone1_Rain" }) },
    { label: "Other weather id...", icon: "cloud", onclick: async () => {
      const id = await promptBox("Set weather", { label: "Weather id", placeholder: "Zone1_Rain", help: "Weather ids come from the game's assets, for example Zone1_Rain. Use weather get in the console to see the current one.", okLabel: "Set weather" });
      if (id) act("world/weather", { id });
    } },
    "-",
    { label: "Show current weather", icon: "info", onclick: () => act("command", { line: "weather get" }) },
  ]);
}

async function announce() {
  const text = await promptBox("Announce to everyone", { label: "Message", multiline: true, okLabel: "Send notice", help: "Shown as an on-screen notice to every player." });
  if (text) act("say", { message: text, style: "notify" }, "Notice sent");
}

export function mountConsole(root) {
  out = h("div", { class: "con-out", role: "log", "aria-live": "off", tabindex: "0" });
  out.addEventListener("scroll", () => {
    state.stick = atBottom();
    if (state.stick) { state.unseen = 0; updateNewBtn(); }
  });
  newBtn = h("button", { class: "btn cyan sm newlines hidden", onclick: () => { out.scrollTop = out.scrollHeight; state.stick = true; state.unseen = 0; updateNewBtn(); } }, icon("down", "sm"), h("span", { class: "lbl" }, ""));

  const seg = h("div", { class: "seg", role: "group", "aria-label": "Filter" }, FILTERS.map((f) =>
    h("button", { "aria-pressed": String(state.filter === f.id), "data-f": f.id, onclick: () => {
      state.filter = f.id; localSet("con-filter", f.id);
      seg.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.f === f.id)));
      rebuild();
    } }, f.label)));

  const search = h("input", { class: "input sm con-search", type: "search", placeholder: "Search console", "aria-label": "Search console",
    oninput: debounce((e) => { state.search = e.target.value.trim().toLowerCase(); rebuild(); }, 150) });

  const viewBtn = h("button", { class: "btn ghost icon sm", "aria-label": "View options", title: "View options", onclick: (e) => menu(e.currentTarget, [
    { label: state.times ? "Hide times" : "Show times", icon: "clock", onclick: () => { state.times = !state.times; localSet("con-times", state.times ? "1" : "0"); out.classList.toggle("no-time", !state.times); } },
    { label: state.wrap ? "Don't wrap long lines" : "Wrap long lines", icon: "layers", onclick: () => { state.wrap = !state.wrap; localSet("con-wrap", state.wrap ? "1" : "0"); out.classList.toggle("nowrap", !state.wrap); } },
    { label: state.raw ? "Tidy log lines" : "Show raw log lines", icon: "terminal", onclick: () => { state.raw = !state.raw; localSet("con-raw", state.raw ? "1" : "0"); rebuild(); } },
    "-",
    { label: "Copy what's shown", icon: "copy", onclick: () => { navigator.clipboard.writeText([...out.querySelectorAll(".ln")].map((l) => l.textContent).join("\n")); toast("Copied"); } },
    { label: "Clear the view", icon: "trash", onclick: () => { S.console = []; rebuild(); } },
    { label: "Open the logs folder", icon: "folder", onclick: () => act("open", { which: "logs" }) },
  ]) }, icon("more"));

  input = h("input", { class: "input", id: "cmd", autocomplete: "off", spellcheck: "false", "aria-label": "Console command",
    oninput: () => { sel = -1; showSuggest(); }, onkeydown: onKey, onfocus: refreshKnown, onblur: () => setTimeout(hideSuggest, 100) });
  suggestBox = h("div", { class: "suggest hidden", role: "listbox" });
  modeBtn = h("button", { class: "btn sm", "aria-label": "Send as", onclick: (e) => menu(e.currentTarget, MODES.map((m) => ({ label: m.label, icon: m.id === "cmd" ? "terminal" : m.id === "say" ? "message" : "megaphone", hint: m.id === state.mode ? "current" : "", onclick: () => setMode(m.id) }))) },
    h("span", { class: "lbl" }, "Command"), icon("down", "sm"));

  const quick = h("div", { class: "quick" },
    h("button", { class: "chip", onclick: () => act("command", { line: "who" }) }, icon("users"), "Who's online"),
    h("button", { class: "chip", onclick: () => act("world/save", {}, "Saving the world") }, icon("save"), "Save world"),
    h("button", { class: "chip", onclick: (e) => timeMenu(e.currentTarget) }, icon("sun"), "Time"),
    h("button", { class: "chip", onclick: (e) => weatherMenu(e.currentTarget) }, icon("cloud"), "Weather"),
    h("button", { class: "chip", onclick: announce }, icon("megaphone"), "Announce"),
    h("button", { class: "chip", onclick: () => act("backups/create", {}, "Backing up the world") }, icon("archive"), "Back up now"),
    h("button", { class: "chip", title: "Asks the server to unload chunks no player needs", onclick: () => act("chunks/unload", {}, "Asked the server to unload unused chunks") }, icon("layers"), "Unload idle chunks"));

  root.append(
    h("div", { class: "frame-head" },
      h("h2", { class: "frame-title" }, "Console"),
      h("div", { class: "grow" }),
      h("div", { class: "con-tools" }, seg, search, viewBtn)),
    h("div", { id: "notices" }),
    h("div", { style: { position: "relative", flex: "1 1 auto", minHeight: 0, display: "flex", flexDirection: "column" } }, out, newBtn),
    h("div", { class: "cmdbar" },
      h("div", { class: "cmdline" }, modeBtn,
        h("div", { class: "prompt-wrap" }, h("span", { class: "chev" }, ">"), input, suggestBox),
        h("button", { class: "btn primary", onclick: submit, "aria-label": "Send" }, icon("send"), h("span", { class: "lbl" }, "Send"))),
      quick));
  setMode("cmd");

  on("snapshot", rebuild);
  on("line", enqueue);
  on("server", () => { if (!S.console.length) rebuild(); });
  document.addEventListener("keydown", (e) => {
    if (e.key === "/" && document.activeElement === document.body) { e.preventDefault(); input.focus(); }
  });
}

export function focusCommand(text) {
  setMode("cmd");
  input.value = text;
  input.focus();
  showSuggest();
}
