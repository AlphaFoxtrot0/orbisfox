// The side dock: one panel at a time, chosen from the hexagon rail.
import { S, on, isRunning, isAlive } from "./store.js";
import {
  h, icon, api, act, toast, modal, confirmBox, promptBox, menu, clear, bytes, ago, duration, dateTime,
  distance, num, initials,
} from "./util.js";

const rule = (label, ...actions) => h("div", { class: "rule" }, h("span", { class: "gem" }), h("span", { class: "label" }, label),
  actions.length ? h("div", { class: "actions" }, actions) : null);

const needServer = (what = "this") => h("div", { class: "note warn" }, icon("info"),
  h("div", null, `Start the server to change ${what}. OrbisFox sends these changes as server commands.`));

function bridgeNote(feature) {
  const b = S.server.bridge || {};
  if (b.connected) return null;
  return h("div", { class: "note" }, icon("plug"), h("div", { class: "grow" },
    h("b", null, "Needs the OrbisFox Bridge plugin. "),
    `${feature} come from a small plugin inside the server. `,
    b.installed ? "It's installed; it connects once the server is running." : h("a", { href: "#", onclick: (e) => { e.preventDefault(); window.orbis.openPanel("mods"); } }, "Install it")));
}

// ============================================================================ players
function pingQ(ms) { return ms == null ? 0 : ms < 60 ? 4 : ms < 120 ? 3 : ms < 220 ? 2 : 1; }
function pingEl(ms) {
  return h("span", { class: "row", style: { gap: "6px" }, title: ms == null ? "No ping yet" : `${ms} ms` },
    h("span", { class: "ping-ms num" }, ms == null ? "–" : `${ms} ms`),
    h("span", { class: "ping", "data-q": String(pingQ(ms)) }, h("i"), h("i"), h("i"), h("i")));
}

function isOp(uuid) { return (S.access.ops || []).some((o) => o.uuid === uuid); }
function isWl(uuid) { return ((S.access.whitelist || {}).entries || []).some((o) => o.uuid === uuid); }
function isBanned(uuid) { return (S.access.bans || []).some((o) => o.uuid === uuid); }

function playerMenu(anchor, p) {
  const name = p.name;
  const run = (body, msg) => act("players/action", { name, ...body }, msg);
  menu(anchor, [
    { label: "Kick", icon: "kick", onclick: async () => { if (await confirmBox(`Kick ${name}?`, "They can rejoin straight away.", "Kick")) run({ action: "kick" }, `Kicked ${name}`); } },
    { label: "Ban...", icon: "ban", danger: true, onclick: () => banFlow(name) },
    "-",
    isOp(p.uuid) ? { label: "Remove operator", icon: "crown", onclick: () => run({ action: "op", on: false }, `${name} is no longer an operator`) }
      : { label: "Make operator", icon: "crown", onclick: () => run({ action: "op", on: true }, `${name} is now an operator`) },
    isWl(p.uuid) ? { label: "Remove from whitelist", icon: "shield", onclick: () => run({ action: "whitelist", on: false }) }
      : { label: "Add to whitelist", icon: "shield", onclick: () => run({ action: "whitelist", on: true }) },
    "-",
    { label: "Adventure mode", icon: "gamepad", onclick: () => run({ action: "gamemode", mode: "adventure" }, `${name}: adventure mode`) },
    { label: "Creative mode", icon: "cube", onclick: () => run({ action: "gamemode", mode: "creative" }, `${name}: creative mode`) },
    { label: "Teleport to player...", icon: "locate", onclick: async () => {
      const t = await promptBox(`Teleport ${name}`, { label: "To player", placeholder: "Player name", okLabel: "Teleport" });
      if (t) run({ action: "tp", target: t });
    } },
    { label: "Give item...", icon: "gift", onclick: () => giveFlow(name) },
    { label: "Where are they?", icon: "map", onclick: () => run({ action: "locate" }) },
  ]);
}

async function banFlow(name) {
  const reason = await promptBox(`Ban ${name}`, { label: "Reason (optional)", placeholder: "Griefing", okLabel: "Ban player", help: "They are disconnected and can't rejoin until unbanned." });
  if (reason !== null) act("players/action", { action: "ban", name, reason }, `Banned ${name}`);
}

function giveFlow(name) {
  const item = h("input", { class: "input", placeholder: "Item id, e.g. Weapon_Sword_Iron" });
  const qty = h("input", { class: "input", type: "number", min: 1, max: 9999, value: 1 });
  modal({
    title: `Give ${name} an item`,
    body: h("div", null, h("div", { class: "field" }, h("span", { class: "label" }, "Item id"), item,
      h("span", { class: "help" }, "Item ids come from the game's assets. In game, an operator can find them in the creative inventory.")),
    h("div", { class: "field" }, h("span", { class: "label" }, "Quantity"), qty)),
    actions: [{ label: "Cancel" }, { label: "Give", cls: "primary", icon: "gift", onclick: () => act("players/action", { action: "give", name, item: item.value.trim(), qty: +qty.value }, "Item given") }],
  });
}

export async function openPlayer(uuidOrName) {
  let tbl = null;
  try { tbl = await api("stats"); } catch { /* offline */ }
  const online = (S.players.online || []).find((p) => p.uuid === uuidOrName || p.name === uuidOrName);
  const rec = tbl && tbl.players.find((p) => p.uuid === uuidOrName || p.name === uuidOrName);
  const p = { ...(rec || {}), ...(online || {}) };
  if (!p.name) { toast("Player not found"); return; }
  const st = (rec && rec.stats) || {};
  const tags = [online ? h("span", { class: "tag cyan" }, "Online") : h("span", { class: "tag" }, "Offline")];
  if (isOp(p.uuid)) tags.push(h("span", { class: "tag fox" }, "Operator"));
  if (isWl(p.uuid)) tags.push(h("span", { class: "tag" }, "Whitelisted"));
  if (isBanned(p.uuid)) tags.push(h("span", { class: "tag ember" }, "Banned"));
  const pos = online && online.x != null ? `${Math.round(online.x)}, ${Math.round(online.y)}, ${Math.round(online.z)}` : rec && rec.saved && rec.saved.x != null ? `${Math.round(rec.saved.x)}, ${Math.round(rec.saved.y)}, ${Math.round(rec.saved.z)} (last saved)` : "–";
  const facts = [
    ["Session", online ? duration(online.session) : "–"], ["Ping", online && online.ping != null ? `${online.ping} ms` : "–"],
    ["World", (online && online.world) || (rec && rec.saved && rec.saved.world) || "–"], ["Position", pos],
    ["Total playtime", rec ? duration(rec.playtime) : "–"], ["Sessions", rec ? num(rec.sessions) : "–"],
    ["First seen", rec && rec.first_seen ? dateTime(rec.first_seen) : "–"], ["Last seen", online ? "now" : rec && rec.last_seen ? ago(rec.last_seen) : "–"],
  ];
  const stats = [["Distance", distance(st.distance), "boot"], ["Deaths", num(st.deaths), "skull"], ["Mobs killed", num(st.mob_kills), "sword"],
    ["Players killed", num(st.player_kills), "target"], ["Blocks broken", num(st.blocks_broken), "pick"], ["Blocks placed", num(st.blocks_placed), "cube"]];
  const run = (body, msg) => act("players/action", { name: p.name, ...body }, msg);
  const body = h("div", null,
    h("div", { class: "row", style: { gap: "16px", marginBottom: "16px" } },
      h("div", { class: `hexav ${isOp(p.uuid) ? "op" : online ? "" : "off"}`, style: { width: "64px", height: "72px", fontSize: "24px" } }, initials(p.name)),
      h("div", { class: "col", style: { gap: "6px" } }, h("div", { style: { fontSize: "28px", lineHeight: "34px", fontWeight: 700 } }, p.name),
        h("div", { class: "row wrap" }, tags), h("div", { class: "muted mono", style: { fontSize: "12px" } }, p.uuid || ""))),
    h("div", { class: "stat-row", style: { marginBottom: "16px" } }, facts.map(([k, v]) => h("div", { class: "stat" }, h("div", { class: "label" }, k), h("div", { style: { fontSize: "17px", fontWeight: 600, marginTop: "2px" } }, v)))),
    rule("Stats"),
    h("div", { class: "stat-row" }, stats.map(([k, v, ic]) => h("div", { class: "stat" }, h("div", { class: "label row", style: { gap: "6px" } }, icon(ic, "sm"), k), h("div", { class: "v" }, v)))),
    bridgeNote("Distance, kills and deaths"),
    rule("Actions"),
    isRunning() ? h("div", { class: "row wrap" },
      online ? h("button", { class: "btn sm", onclick: () => run({ action: "kick" }, `Kicked ${p.name}`) }, icon("kick", "sm"), "Kick") : null,
      isBanned(p.uuid) ? h("button", { class: "btn sm", onclick: () => run({ action: "unban" }, `Unbanned ${p.name}`) }, icon("ban", "sm"), "Unban")
        : h("button", { class: "btn sm danger", onclick: () => banFlow(p.name) }, icon("ban", "sm"), "Ban"),
      h("button", { class: "btn sm", onclick: () => run({ action: "op", on: !isOp(p.uuid) }) }, icon("crown", "sm"), isOp(p.uuid) ? "Remove operator" : "Make operator"),
      h("button", { class: "btn sm", onclick: () => run({ action: "whitelist", on: !isWl(p.uuid) }) }, icon("shield", "sm"), isWl(p.uuid) ? "Un-whitelist" : "Whitelist"),
      online ? h("button", { class: "btn sm", onclick: (e) => playerMenu(e.currentTarget, p) }, icon("more", "sm"), "More") : null)
      : needServer("players"));
  modal({ title: "Player", body });
}

function renderPlayers(body) {
  const online = S.players.online || [];
  const max = S.server.max_players;
  const items = online.map((p) => h("div", { class: "item clickable", onclick: (e) => { if (!e.target.closest(".acts")) openPlayer(p.uuid); } },
    h("div", { class: `hexav ${p.op ? "op" : ""}` }, initials(p.name)),
    h("div", { class: "main" },
      h("div", { class: "name" }, p.name, p.op ? h("span", { class: "tag fox", style: { marginLeft: "8px" } }, "OP") : null),
      h("div", { class: "sub" }, [p.world, duration(p.session), p.x != null ? `${Math.round(p.x)}, ${Math.round(p.z)}` : null].filter(Boolean).join("  ·  "))),
    pingEl(p.ping),
    h("div", { class: "acts" },
      h("button", { class: "btn ghost icon xs", "aria-label": `Kick ${p.name}`, title: "Kick", onclick: async () => { if (await confirmBox(`Kick ${p.name}?`, "They can rejoin straight away.", "Kick")) act("players/action", { action: "kick", name: p.name }, `Kicked ${p.name}`); } }, icon("kick", "sm")),
      h("button", { class: "btn ghost icon xs", "aria-label": `More for ${p.name}`, onclick: (e) => playerMenu(e.currentTarget, p) }, icon("more", "sm")))));
  clear(body,
    h("div", { class: "stat-row", style: { marginTop: "8px" } },
      h("div", { class: "stat" }, h("div", { class: "label" }, "Online"), h("div", { class: "v" }, String(online.length), max ? h("small", null, ` / ${max}`) : null)),
      h("div", { class: "stat" }, h("div", { class: "label" }, "Avg ping"), h("div", { class: "v" }, (() => { const v = online.filter((p) => p.ping != null); return v.length ? `${Math.round(v.reduce((a, p) => a + p.ping, 0) / v.length)}` : "–"; })(), h("small", null, " ms")))),
    rule("Online now"),
    online.length ? h("div", { class: "list" }, items) : h("div", { class: "empty" }, isRunning() ? "Nobody is online right now." : "The server is not running."),
    bridgeNote("Pings and positions"),
    rule("Recently seen"),
    h("div", { class: "list", id: "recent-players" }, h("div", { class: "empty" }, "Loading...")));
  api("stats").then((t) => {
    const el = body.querySelector("#recent-players");
    if (!el) return;
    const recent = t.players.filter((p) => !p.online).sort((a, b) => (b.last_seen || 0) - (a.last_seen || 0)).slice(0, 10);
    clear(el, recent.length ? recent.map((p) => h("div", { class: "item clickable", onclick: () => openPlayer(p.uuid) },
      h("div", { class: "hexav off" }, initials(p.name)),
      h("div", { class: "main" }, h("div", { class: "name" }, p.name), h("div", { class: "sub" }, `Last seen ${ago(p.last_seen)}  ·  ${duration(p.playtime)} played`)))) : h("div", { class: "empty" }, "No one yet."));
  }).catch(() => {});
}

// ============================================================================ access
let accessTab = "whitelist";
function renderAccess(body) {
  const a = S.access || {};
  const wl = a.whitelist || { entries: [] };
  const running = isRunning();
  const tabs = h("div", { class: "seg", style: { margin: "8px 0 4px" } }, [["whitelist", "Whitelist", (wl.entries || []).length], ["ops", "Operators", (a.ops || []).length], ["bans", "Bans", (a.bans || []).length]].map(([id, label, n]) =>
    h("button", { "aria-pressed": String(accessTab === id), onclick: () => { accessTab = id; renderAccess(body); } }, label, h("span", { class: "count" }, n))));
  const addRow = (placeholder, onAdd, extra) => {
    const inp = h("input", { class: "input sm", placeholder, disabled: !running, onkeydown: (e) => { if (e.key === "Enter") go(); } });
    const go = async () => { const v = inp.value.trim(); if (!v) return; if (await onAdd(v, extra && extra.value) !== undefined) { inp.value = ""; if (extra) extra.value = ""; } };
    return h("div", { class: "col", style: { marginBottom: "8px" } }, h("div", { class: "row" }, inp, h("button", { class: "btn sm primary", disabled: !running, onclick: go }, icon("plus", "sm"), "Add")), extra || null);
  };
  const nameOf = (e) => e.name || (e.uuid ? e.uuid.slice(0, 8) + "..." : "?");
  let content;
  if (accessTab === "whitelist") {
    const sw = h("input", { type: "checkbox", checked: !!wl.enabled, disabled: !running, onchange: (e) => act("access/whitelist", { action: e.target.checked ? "enable" : "disable" }, e.target.checked ? "Whitelist on: only listed players can join" : "Whitelist off: anyone can join") });
    content = [
      h("div", { class: "card flat", style: { margin: "12px 0" } }, h("label", { class: "switch" }, sw, h("span", { class: "track" }),
        h("span", null, h("b", null, "Only whitelisted players can join"), h("div", { class: "muted", style: { fontSize: "15px" } }, wl.enabled == null ? "Status unknown until the server writes its settings." : wl.enabled ? "On: everyone else is turned away." : "Off: anyone can join.")))),
      running ? null : needServer("the whitelist"),
      addRow("Player name", (n) => act("access/whitelist", { action: "add", name: n }, `${n} can join`)),
      h("div", { class: "list" }, (wl.entries || []).length ? wl.entries.map((e) => h("div", { class: "item" }, h("div", { class: "hexav off" }, initials(nameOf(e))),
        h("div", { class: "main" }, h("div", { class: "name" }, nameOf(e))),
        h("div", { class: "acts" }, h("button", { class: "btn ghost icon xs", "aria-label": `Remove ${nameOf(e)}`, disabled: !running || !e.name, title: e.name ? "Remove" : "Name unknown: remove it in the console with the player's name", onclick: () => act("access/whitelist", { action: "remove", name: e.name }) }, icon("x", "sm")))))
        : h("div", { class: "empty" }, "Nobody is on the whitelist.")),
    ];
  } else if (accessTab === "ops") {
    content = [
      h("p", { class: "muted", style: { margin: "12px 0" } }, "Operators can use every command in game."),
      running ? null : needServer("operators"),
      addRow("Player name", (n) => act("players/action", { action: "op", name: n, on: true }, `${n} is now an operator`)),
      h("div", { class: "list" }, (a.ops || []).length ? a.ops.map((e) => h("div", { class: "item" }, h("div", { class: "hexav op" }, initials(nameOf(e))),
        h("div", { class: "main" }, h("div", { class: "name" }, nameOf(e))),
        h("div", { class: "acts" }, h("button", { class: "btn ghost icon xs", "aria-label": `Remove ${nameOf(e)}`, disabled: !running || !e.name, onclick: () => act("players/action", { action: "op", name: e.name, on: false }, `${e.name} is no longer an operator`) }, icon("x", "sm")))))
        : h("div", { class: "empty" }, "No operators yet.")),
    ];
  } else {
    const reason = h("input", { class: "input sm", placeholder: "Reason (optional)", disabled: !running });
    content = [
      running ? null : needServer("bans"),
      h("div", { style: { height: "12px" } }),
      addRow("Player name", (n, r) => act("players/action", { action: "ban", name: n, reason: r }, `Banned ${n}`), reason),
      h("div", { class: "list" }, (a.bans || []).length ? a.bans.map((e) => h("div", { class: "item" }, h("div", { class: "hexav off" }, initials(nameOf(e))),
        h("div", { class: "main" }, h("div", { class: "name" }, nameOf(e)), h("div", { class: "sub" }, [e.reason, e.since ? String(e.since).slice(0, 10) : null, e.until ? `until ${String(e.until).slice(0, 10)}` : null].filter(Boolean).join("  ·  "))),
        h("div", { class: "acts" }, h("button", { class: "btn xs", disabled: !running || !e.name, onclick: () => act("players/action", { action: "unban", name: e.name }, `Unbanned ${e.name}`) }, "Unban"))))
        : h("div", { class: "empty" }, "Nobody is banned.")),
    ];
  }
  clear(body, tabs, content);
}

// ============================================================================ world
function dial(onPick) {
  const NS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", "0 0 340 200");
  svg.setAttribute("class", "dial");
  svg.setAttribute("role", "group");
  svg.setAttribute("aria-label", "Time of day");
  svg.innerHTML = `
    <defs>
      <linearGradient id="sky" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#1B3A40"/><stop offset="1" stop-color="#376E77"/></linearGradient>
      <linearGradient id="night" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#0A1416"/><stop offset="1" stop-color="#050B0C"/></linearGradient>
    </defs>
    <path d="M30 150 A140 140 0 0 1 310 150 Z" fill="url(#sky)" opacity="0.55"/>
    <path d="M30 150 A140 140 0 0 0 310 150" fill="none" stroke="#2F4446" stroke-dasharray="3 5"/>
    <path d="M30 150 A140 140 0 0 1 310 150" fill="none" stroke="#E8B029" stroke-opacity="0.55" stroke-width="1.5"/>
    <line x1="14" y1="150" x2="326" y2="150" stroke="#5E8A91" stroke-width="1.5"/>
    <path d="M170 150 l-5 -5 5 -5 5 5z" fill="#E8B029"/>`;
  const presets = [["dawn", 30, 150, "Dawn"], ["midday", 170, 10, "Midday"], ["dusk", 310, 150, "Dusk"], ["midnight", 170, 190, "Midnight"]];
  for (const [id, x, y, label] of presets) {
    const g = document.createElementNS(NS, "g");
    g.setAttribute("tabindex", "0");
    g.setAttribute("role", "button");
    g.setAttribute("aria-label", `Set time to ${label}`);
    g.style.cursor = "pointer";
    const night = id === "midnight";
    const r = id === "midday" ? 16 : 13;
    g.innerHTML = `<circle cx="${x}" cy="${y}" r="${r + 9}" fill="transparent"/>` +
      (night ? `<circle cx="${x}" cy="${y - 4}" r="${r}" fill="#E2D3C0"/><circle cx="${x + 6}" cy="${y - 8}" r="${r - 2}" fill="#101A1C"/>`
        : `<circle cx="${x}" cy="${y}" r="${r}" fill="${id === "midday" ? "#E8B029" : "#EA6816"}"/>`) +
      `<text x="${x}" y="${night ? y - 22 : id === "midday" ? y + 34 : y - 22}" text-anchor="middle" fill="#E2D3C0" font-size="15" font-weight="600">${label}</text>`;
    const pick = () => onPick(id);
    g.addEventListener("click", pick);
    g.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(); } });
    svg.append(g);
  }
  return svg;
}

let worldCfg = null;
async function renderWorld(body) {
  const running = isRunning();
  const timeCard = h("div", { class: "dial-wrap" },
    dial((id) => running ? act("world/time", { preset: id }, `Time set to ${id}`) : toast("Start the server first", "", "warn")),
    h("div", { class: "row" }, h("button", { class: "btn sm", disabled: !running, onclick: () => act("world/time", { pause: true }) }, icon("pause", "sm"), "Pause / resume time")));
  const weather = h("div", { class: "row wrap" },
    h("button", { class: "btn sm", disabled: !running, onclick: () => act("world/weather", { id: "reset" }, "Weather back to natural") }, icon("sun", "sm"), "Natural"),
    h("button", { class: "btn sm", disabled: !running, onclick: () => act("world/weather", { id: "Zone1_Rain" }) }, icon("rain", "sm"), "Rain"),
    h("button", { class: "btn sm", disabled: !running, onclick: async () => { const id = await promptBox("Set weather", { label: "Weather id", placeholder: "Zone1_Rain", okLabel: "Set" }); if (id) act("world/weather", { id }); } }, icon("cloud", "sm"), "Other..."));
  const cfgBox = h("div", null, h("div", { class: "empty" }, "Loading server settings..."));
  clear(body, rule("Time of day"), timeCard, rule("Weather"), weather, rule("Server settings"), cfgBox);
  try { worldCfg = await api("world/config"); } catch (e) { clear(cfgBox, h("div", { class: "note" }, icon("info"), e.message)); return; }
  if (!worldCfg.exists) { clear(cfgBox, h("div", { class: "note" }, icon("info"), "config.json appears after the server's first start.")); return; }
  const c = worldCfg.config;
  const alive = worldCfg.running;
  const f = {};
  const field = (key, label, el, help) => { f[key] = el; return h("label", { class: "field" }, h("span", { class: "label" }, label), el, help ? h("span", { class: "help" }, help) : null); };
  const vr = h("input", { type: "range", min: 4, max: 32, value: c.MaxViewRadius || 12, style: { width: "100%", accentColor: "var(--fox)" } });
  const vrLabel = h("span", { class: "num" });
  const upd = () => { const n = +vr.value; vrLabel.textContent = `${n} chunks (${n * 32} blocks)${n > 12 ? "  ·  more memory" : n === 12 ? "  ·  recommended" : ""}`; };
  vr.addEventListener("input", upd); upd();
  f.MaxViewRadius = vr;
  const lockNote = alive ? h("div", { class: "note", style: { marginBottom: "16px" } }, icon("info"), h("div", null, "View distance and player limit change live. Stop the server to change the rest: it rewrites its settings file while running.")) : null;
  clear(cfgBox, lockNote,
    h("div", { class: "field" }, h("span", { class: "label" }, "View distance"), vr, h("span", { class: "help" }, vrLabel, h("br"), "The biggest driver of memory use. Hytale recommends 12.")),
    field("MaxPlayers", "Player limit", h("input", { class: "input", type: "number", min: 1, max: 1000, value: c.MaxPlayers ?? 20 })),
    field("ServerName", "Server name", h("input", { class: "input", value: c.ServerName || "", disabled: alive })),
    field("MOTD", "Message of the day", h("input", { class: "input", value: c.MOTD || "", disabled: alive })),
    field("Password", "Join password", h("input", { class: "input", type: "password", value: c.Password || "", placeholder: "No password", disabled: alive, autocomplete: "new-password" }), "Players must enter this to join. Leave empty for none."),
    field("GameMode", "Default game mode", h("select", { class: "input", disabled: alive }, ["Adventure", "Creative"].map((m) => h("option", { value: m, selected: c.GameMode === m }, m)))),
    h("div", { class: "row" }, h("div", { class: "grow" }), h("button", { class: "btn primary", onclick: async () => {
      const changes = {};
      for (const [k, el] of Object.entries(f)) {
        if (el.disabled) continue;
        const v = el.type === "number" || el.type === "range" ? +el.value : el.value;
        if (String(v) !== String(c[k] ?? "")) changes[k] = v;
      }
      if (!Object.keys(changes).length) { toast("Nothing changed"); return; }
      const r = await act("world/config", { changes });
      if (r) { toast("Saved", r.live && r.live.length ? `Changed live: ${r.live.join(", ")}.` : "Used from the next start."); renderWorld(body); }
    } }, icon("save"), "Save settings")),
    worldCfg.worlds.length ? [rule("Worlds"), h("div", { class: "list" }, worldCfg.worlds.map((w) => h("div", { class: "item" }, icon("map"),
      h("div", { class: "main" }, h("div", { class: "name" }, w.name), h("div", { class: "sub" }, [w.seed != null ? `Seed ${w.seed}` : null, w.pvp ? "PvP on" : "PvP off"].filter(Boolean).join("  ·  "))))))] : null);
}

// ============================================================================ backups
function renderBackups(body) {
  const b = S.backups || {};
  const busy = b.busy;
  const pct = busy && busy.total ? Math.min(100, (busy.done / busy.total) * 100) : 0;
  const restore = async (name) => {
    if (isAlive()) {
      if (!(await confirmBox("Stop the server and restore?", `The server has to be stopped to restore ${name}. OrbisFox backs up the current world first, so you can undo this.`, "Stop and restore", true))) return;
      await act("server/stop", { warn_seconds: 0 });
      for (let i = 0; i < 120 && isAlive(); i++) await new Promise((r) => setTimeout(r, 1000));
      if (isAlive()) { toast("The server did not stop", "", "bad"); return; }
      act("backups/restore", { name, start: true }, "Restoring... the server starts again when done");
      return;
    }
    if (await confirmBox("Restore this backup?", `The current world is backed up first, then replaced with ${name}.`, "Restore", true)) act("backups/restore", { name }, "Restoring...");
  };
  const rows = (b.list || []).map((x) => h("div", { class: "item" }, icon("archive"),
    h("div", { class: "main" }, h("div", { class: "name" }, dateTime(x.created)),
      h("div", { class: "sub" }, `${bytes(x.size)}  ·  ${ago(x.created)}`)),
    h("span", { class: `tag ${x.reason === "manual" ? "cyan" : ""}` }, x.reason.replace(/-/g, " ")),
    h("div", { class: "acts" }, h("button", { class: "btn ghost icon xs", "aria-label": "Backup options", onclick: (e) => menu(e.currentTarget, [
      { label: "Restore...", icon: "restart", onclick: () => restore(x.name) },
      { label: "Download", icon: "download", onclick: () => { const a = h("a", { href: `/api/backups/download?name=${encodeURIComponent(x.name)}&token=${encodeURIComponent(window.orbis.token())}`, download: x.name }); document.body.append(a); a.click(); a.remove(); } },
      "-",
      { label: "Delete", icon: "trash", danger: true, onclick: async () => { if (await confirmBox("Delete this backup?", `${x.name} will be gone for good.`, "Delete", true)) act("backups/delete", { name: x.name }); } },
    ]) }, icon("more", "sm")))));
  const s = S.settings.backups || {};
  clear(body,
    h("div", { class: "card", style: { marginTop: "8px" } },
      h("div", { class: "label" }, "Last backup"),
      h("div", { class: "stat-big" }, b.last ? ago(b.last) : "Never"),
      h("div", { class: "muted", style: { marginBottom: "12px" } }, b.last ? dateTime(b.last) : "Back up before you change anything big."),
      busy ? h("div", { class: "col" }, h("div", { class: "row" }, h("span", { class: "grow" }, busy.kind === "restore" ? "Restoring..." : "Backing up..."), h("span", { class: "num muted" }, `${pct.toFixed(0)}%`)), h("div", { class: "meter" }, h("i", { style: { width: `${pct}%` } })))
        : h("div", { class: "row wrap" }, h("button", { class: "btn primary", onclick: () => act("backups/create", {}, "Backing up the world") }, icon("archive"), "Back up now"),
          h("button", { class: "btn", onclick: () => act("open", { which: "backups" }) }, icon("folder"), "Open folder"))),
    h("p", { class: "muted", style: { fontSize: "15px" } },
      `Automatic: ${s.every_minutes ? `every ${s.every_minutes} min` : "off"}, before updates ${s.before_update ? "on" : "off"}, keeps the newest ${s.keep}. `,
      h("a", { href: "#", onclick: (e) => { e.preventDefault(); window.orbis.openPanel("settings"); } }, "Change")),
    rule("OrbisFox backups"),
    rows.length ? h("div", { class: "list" }, rows) : h("div", { class: "empty" }, "No backups yet."),
    rule("Server's own backups", h("button", { class: "btn ghost xs", disabled: !isRunning(), onclick: () => act("backups/create", { builtin: true }, "The server is making a backup") }, icon("plus", "sm"), "Run now"),
      h("button", { class: "btn ghost xs", onclick: () => act("open", { which: "builtin-backups" }) }, icon("folder", "sm"), "Open")),
    h("p", { class: "muted", style: { fontSize: "15px", marginTop: 0 } }, s.hytale_builtin ? `The server also backs itself up every ${s.hytale_frequency} minutes. Restore these by hand from the folder.` : "Off. Turn it on in Settings."),
    (b.builtin || []).length ? h("div", { class: "list" }, b.builtin.slice(0, 10).map((x) => h("div", { class: "item" }, icon("archive"),
      h("div", { class: "main" }, h("div", { class: "name" }, x.name), h("div", { class: "sub" }, `${bytes(x.size)}  ·  ${ago(x.created)}`))))) : null);
}

// ============================================================================ updates
function renderUpdates(body) {
  const u = S.updates || {};
  const latest = u.latest || {};
  const steps = u.steps || [];
  const big = h("div", { class: "card", style: { marginTop: "8px" } },
    h("div", { class: "row" },
      h("div", { class: "grow" }, h("div", { class: "label" }, "Installed"), h("div", { class: "stat-big" }, u.installed || "–")),
      h("div", { class: "grow" }, h("div", { class: "label" }, `Latest ${u.patchline === "pre-release" ? "pre-release" : "release"}`),
        h("div", { class: "stat-big", style: { color: u.available ? "var(--cyan)" : "var(--ink)" } }, latest.version || "–"))),
    h("div", { class: "muted", style: { margin: "4px 0 12px" } }, u.error ? u.error : u.checked_at ? `Checked ${ago(u.checked_at)}.` : "Not checked yet.",
      u.available === false ? " You're up to date." : ""),
    h("div", { class: "row wrap" },
      u.available ? h("button", { class: "btn primary", disabled: u.busy, onclick: () => installFlow(u) }, icon("download"), "Install update") : null,
      h("button", { class: "btn", disabled: u.busy, onclick: () => act("updates/check", {}) }, icon("refresh"), "Check now")));
  const auth = u.auth ? h("div", { class: "note warn", style: { marginTop: "12px" } }, icon("key"), h("div", null,
    h("b", null, "Sign in to download: "), u.auth.url ? h("a", { href: u.auth.url, target: "_blank", rel: "noopener" }, u.auth.url) : "see the console",
    u.auth.code ? [" and enter ", h("b", { class: "mono", style: { color: "var(--ink)" } }, u.auth.code)] : null)) : null;
  const prog = steps.length ? [rule(u.busy ? "Installing" : "Last install"), h("div", { class: "steps" }, steps.map((s, i) =>
    h("div", { class: `step ${s.state === "active" ? "active" : s.state === "done" ? "done" : s.state === "fail" ? "fail" : ""}` },
      h("span", { class: "b" }, s.state === "done" ? "✓" : s.state === "fail" ? "!" : s.state === "skip" ? "–" : String(i + 1)), s.label))),
    u.message ? h("p", { class: "muted" }, u.message) : null] : null;
  const patch = h("select", { class: "input sm", style: { width: "auto" }, onchange: async (e) => {
    await act("settings", { changes: { updates: { patchline: e.target.value } } });
    act("updates/check", {});
  } }, [["release", "Release"], ["pre-release", "Pre-release (testing)"]].map(([v, l]) => h("option", { value: v, selected: u.patchline === v }, l)));
  const auto = h("input", { type: "checkbox", checked: !!(S.settings.updates || {}).auto_install, onchange: (e) => act("settings", { changes: { updates: { auto_install: e.target.checked } } }) });
  clear(body, big, auth, prog,
    rule("Options"),
    h("label", { class: "field" }, h("span", { class: "label" }, "Update channel"), patch, h("span", { class: "help" }, "Pre-release gets fixes sooner (Update 8's memory fixes are there now) but may have new bugs.")),
    h("label", { class: "switch", style: { marginBottom: "16px" } }, auto, h("span", { class: "track" }), h("span", null, "Install updates automatically when nobody is online")),
    rule("How OrbisFox installs"),
    h("p", { class: "muted", style: { fontSize: "15px", marginTop: 0 } },
      "With the server running, OrbisFox uses the server's own updater: it downloads, backs up, warns players, applies and restarts. ",
      "With it stopped, OrbisFox uses Hytale's official downloader. Your world, settings, mods and the server's sign-in are never touched."),
    h("div", { class: "row wrap" },
      u.downloader ? h("span", { class: "tag cyan" }, "hytale-downloader found") : h("button", { class: "btn sm", onclick: () => act("updates/get-downloader", {}, "Downloaded hytale-downloader") }, icon("download", "sm"), "Get hytale-downloader"),
      u.builtin_ok ? h("span", { class: "tag cyan" }, "Built-in updater available") : h("span", { class: "tag" }, "Built-in updater needs the official folder layout")));
}

async function installFlow(u) {
  const n = (S.players.online || []).length;
  const ok = await confirmBox(`Install ${u.latest.version}?`,
    `OrbisFox backs up the world, ${n ? `warns the ${n} player${n > 1 ? "s" : ""} online, ` : ""}restarts the server on the new version and keeps your world, settings and mods.`, "Install");
  if (ok) act("updates/install", { method: "auto" });
}

// ============================================================================ stats
let statKey = "playtime";
const STAT_CATS = [
  ["playtime", "Playtime", (p) => p.playtime, duration],
  ["distance", "Distance", (p) => p.stats.distance, distance],
  ["mob_kills", "Mobs killed", (p) => p.stats.mob_kills, num],
  ["deaths", "Deaths", (p) => p.stats.deaths, num],
  ["blocks", "Blocks", (p) => (p.stats.blocks_broken || 0) + (p.stats.blocks_placed || 0), num],
];

async function renderStats(body) {
  let t;
  try { t = await api("stats"); } catch (e) { clear(body, h("div", { class: "empty" }, e.message)); return; }
  const cat = STAT_CATS.find((c) => c[0] === statKey);
  const rows = t.players.map((p) => ({ p, v: cat[2](p) || 0 })).sort((a, b) => b.v - a.v).slice(0, 15);
  const max = rows.length ? rows[0].v || 1 : 1;
  const seg = h("div", { class: "seg", style: { margin: "8px 0", flexWrap: "wrap" } }, STAT_CATS.map(([id, label]) =>
    h("button", { "aria-pressed": String(statKey === id), onclick: () => { statKey = id; renderStats(body); } }, label)));
  const evs = (t.events || []).slice(0, 12);
  clear(body, seg,
    rows.length ? h("div", { class: "list" }, rows.map(({ p, v }, i) => h("div", { class: "item clickable", onclick: () => openPlayer(p.uuid) },
      h("span", { class: "num", style: { width: "22px", color: i < 3 ? "var(--amber-text)" : "var(--ink-muted)", fontWeight: 700 } }, String(i + 1)),
      h("div", { class: `hexav ${p.online ? "" : "off"}` }, initials(p.name)),
      h("div", { class: "main" }, h("div", { class: "row" }, h("span", { class: "name grow" }, p.name), h("span", { class: "num", style: { fontWeight: 700 } }, cat[3](v))),
        h("div", { class: "meter", style: { marginTop: "6px" } }, h("i", { style: { width: `${Math.max(2, (v / max) * 100)}%`, background: i === 0 ? "var(--fox)" : "var(--cyan)" } }))))))
      : h("div", { class: "empty" }, "No players yet."),
    h("div", { class: "row", style: { marginTop: "12px" } }, h("button", { class: "btn sm", onclick: () => statsTable(t) }, icon("trophy", "sm"), "Full table")),
    statKey !== "playtime" ? bridgeNote("Distance, kills, deaths and blocks") : null,
    rule("Recent deaths and kills"),
    evs.length ? h("div", { class: "list" }, evs.map((e) => h("div", { class: "item" }, icon(e.type === "death" ? "skull" : "sword"),
      h("div", { class: "main" }, h("div", { class: "name", style: { fontWeight: 400 } },
        e.type === "death" ? [h("b", null, e.player), ` died${e.killer ? ` to ${e.killer}` : e.cause ? ` (${String(e.cause).toLowerCase()})` : ""}`]
          : [h("b", null, e.player), ` defeated ${e.victim || "something"}`]),
        h("div", { class: "sub" }, ago(e.t)))))) : h("div", { class: "empty" }, "Nothing yet."));
}

function statsTable(t) {
  const cols = [["name", "Player", (p) => p.name, (v) => v], ["playtime", "Playtime", (p) => p.playtime, duration], ["sessions", "Sessions", (p) => p.sessions, num],
    ["distance", "Distance", (p) => p.stats.distance, distance], ["deaths", "Deaths", (p) => p.stats.deaths, num], ["mob_kills", "Mobs", (p) => p.stats.mob_kills, num],
    ["player_kills", "PvP kills", (p) => p.stats.player_kills, num], ["blocks_broken", "Broken", (p) => p.stats.blocks_broken, num], ["blocks_placed", "Placed", (p) => p.stats.blocks_placed, num],
    ["last_seen", "Last seen", (p) => p.online ? Infinity : p.last_seen, (v) => v === Infinity ? "online" : ago(v)]];
  let sort = "playtime", dir = -1;
  const wrap = h("div", { style: { overflow: "auto", flex: 1, padding: "0 16px 16px" } });
  const draw = () => {
    const c = cols.find((x) => x[0] === sort);
    const rows = [...t.players].sort((a, b) => { const x = c[2](a) ?? -1, y = c[2](b) ?? -1; return (x > y ? 1 : x < y ? -1 : 0) * dir; });
    clear(wrap, h("table", { class: "tbl" },
      h("thead", null, h("tr", null, h("th", null, "#"), cols.map(([id, label]) => h("th", { class: id === "name" ? "" : "r", "aria-sort": sort === id ? (dir > 0 ? "ascending" : "descending") : null, onclick: () => { if (sort === id) dir = -dir; else { sort = id; dir = id === "name" ? 1 : -1; } draw(); } }, label, sort === id ? (dir > 0 ? " ▲" : " ▼") : "")))),
      h("tbody", null, rows.map((p, i) => h("tr", { style: { cursor: "pointer" }, onclick: () => openPlayer(p.uuid) }, h("td", { class: "rank" }, String(i + 1)),
        cols.map(([id, , get, fmt]) => h("td", { class: id === "name" ? "" : "r" }, id === "name" ? h("span", { class: "row" }, h("span", { class: `hexav ${p.online ? "" : "off"}`, style: { width: "24px", height: "27px", fontSize: "11px" } }, initials(p.name)), p.name) : fmt(get(p)))))))));
  };
  draw();
  modal({ title: "Player stats", wide: true, body: wrap });
}

// ============================================================================ performance
function lineChart(series, { height = 120, max, unit = "" } = {}) {
  const W = 360, H = height;
  const NS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.setAttribute("class", "chart");
  svg.setAttribute("preserveAspectRatio", "none");
  const all = series.flatMap((s) => s.values.filter((v) => v != null));
  const m = max || Math.max(1, ...all) * 1.15;
  let inner = "";
  for (const f of [0.25, 0.5, 0.75]) inner += `<line x1="0" x2="${W}" y1="${H - f * H}" y2="${H - f * H}" stroke="#1B2A2C" stroke-width="1"/>`;
  for (const s of series) {
    const n = s.values.length;
    if (n < 2) continue;
    let d = "", started = false;
    s.values.forEach((v, i) => {
      if (v == null) { started = false; return; }
      const x = (i / (n - 1)) * W, y = H - 2 - (Math.min(v, m) / m) * (H - 4);
      d += `${started ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`;
      started = true;
    });
    if (!d) continue;
    if (s.fill) inner += `<path d="${d}L${W},${H}L0,${H}Z" fill="${s.color}" opacity="0.12"/>`;
    inner += `<path d="${d}" fill="none" stroke="${s.color}" stroke-width="1.6" vector-effect="non-scaling-stroke" ${s.dash ? 'stroke-dasharray="4 4"' : ""}/>`;
  }
  svg.innerHTML = inner;
  const legend = h("div", { class: "chart-legend" }, series.map((s) => {
    const last = [...s.values].reverse().find((v) => v != null);
    return h("span", null, h("i", { style: { background: s.color } }), s.label, last != null ? h("b", { style: { color: "var(--ink)", marginLeft: "6px" } }, `${s.fmt ? s.fmt(last) : last.toFixed(0)}${unit}`) : null);
  }));
  return h("div", { class: "chart-wrap" }, svg, legend);
}

let perfData = null;
async function renderPerf(body, fresh = true) {
  if (fresh || !perfData) { try { perfData = await api("perf"); } catch (e) { clear(body, h("div", { class: "empty" }, e.message)); return; } }
  const wd = S.watchdog && S.watchdog.state ? { ...perfData.watchdog, state: S.watchdog.state, reasons: S.watchdog.reasons || perfData.watchdog.reasons } : perfData.watchdog;
  const hist = S.metrics.history || [];
  const gc = S.gc && S.gc.max_mb ? S.gc : wd.gc || {};
  const idleLabel = isRunning() ? "Warming up" : isAlive() ? "Starting" : "Server stopped";
  const label = { idle: idleLabel, ok: "Healthy", strained: "Under strain", critical: "Overloaded" }[wd.state] || "Watching";
  const col = { ok: "var(--cyan)", strained: "var(--amber-text)", critical: "var(--ember)" }[wd.state] || "var(--ink-muted)";
  const m = S.metrics.latest || {};
  const worlds = m.worlds || perfData.worlds || [];
  const chunks = worlds.reduce((a, w) => a + (w.chunks || 0), 0);
  clear(body,
    h("div", { class: "card", style: { marginTop: "8px" } },
      h("div", { class: "row" }, h("span", { style: { width: "12px", height: "12px", borderRadius: "50%", background: col, flex: "none" } }),
        h("div", { class: "stat-big", style: { color: col } }, label)),
      wd.reasons && wd.reasons.length ? h("ul", { style: { margin: "8px 0 0", paddingLeft: "20px", color: "var(--ink)" } }, wd.reasons.map((r) => h("li", null, r)))
        : h("div", { class: "muted" }, wd.state === "ok" ? "Memory, garbage collection and world ticks look normal." : "OrbisFox watches memory, garbage collection and world ticks while the server runs.")),
    h("div", { class: "stat-row", style: { marginTop: "12px" } },
      h("div", { class: "stat" }, h("div", { class: "label" }, "Heap after GC"), h("div", { class: "v" }, gc.live_percent != null ? `${gc.live_percent.toFixed(0)}%` : "–", gc.max_mb ? h("small", null, ` of ${(gc.max_mb / 1024).toFixed(0)} GB`) : null)),
      h("div", { class: "stat" }, h("div", { class: "label" }, "Time in GC"), h("div", { class: "v" }, gc.gc_overhead_percent != null ? `${gc.gc_overhead_percent.toFixed(1)}%` : "–")),
      h("div", { class: "stat" }, h("div", { class: "label" }, "Loaded chunks"), h("div", { class: "v" }, worlds.length ? num(chunks) : "–")),
      h("div", { class: "stat" }, h("div", { class: "label" }, "View distance"), h("div", { class: "v" }, wd.view_radius != null ? String(wd.view_radius) : S.server.view_radius != null ? String(S.server.view_radius) : "–",
        wd.baseline_radius && wd.view_radius < wd.baseline_radius ? h("small", null, ` of ${wd.baseline_radius}`) : h("small", null, " chunks")))),
    rule("CPU and GPU"),
    lineChart([{ label: "Server CPU", color: "#4DC4D8", values: hist.map((x) => x.cpu), fill: true }, { label: "Whole PC", color: "#5E8A91", values: hist.map((x) => x.sys_cpu), dash: true },
      { label: "Server GPU", color: "#E8B029", values: hist.map((x) => x.gpu) }], { max: 100, unit: "%" }),
    rule("Memory"),
    lineChart([{ label: "Java heap after GC", color: "#D66623", values: hist.map((x) => x.heap_used), fill: true, fmt: (v) => `${(v / 1024).toFixed(1)} GB` },
      { label: "Heap limit", color: "#5E8A91", values: hist.map((x) => x.heap_max), dash: true, fmt: (v) => `${(v / 1024).toFixed(0)} GB` }], { max: (gc.max_mb || 0) * 1.05 || undefined }),
    rule("World"),
    lineChart([{ label: "Tick rate", color: "#4DC4D8", values: hist.map((x) => x.tps), fill: true, fmt: (v) => `${v.toFixed(0)}/30` },
      { label: "Players", color: "#E8B029", values: hist.map((x) => x.players), fmt: (v) => String(v) }], { max: 32 }),
    worlds.length ? h("table", { class: "tbl", style: { marginTop: "12px" } }, h("thead", null, h("tr", null, ["World", "TPS", "Chunks", "Players", "Response"].map((x, i) => h("th", { class: i ? "r" : "" }, x)))),
      h("tbody", null, worlds.map((w) => h("tr", null, h("td", null, w.name), h("td", { class: "r" }, w.tps != null ? w.tps.toFixed(1) : "–"), h("td", { class: "r" }, num(w.chunks)), h("td", { class: "r" }, num(w.players)),
        h("td", { class: "r" }, w.latency_ms != null ? (w.latency_ms >= 1000 ? `${(w.latency_ms / 1000).toFixed(1)} s` : `${w.latency_ms} ms`) : "–"))))) : bridgeNote("Per-world tick rate, chunk counts and response times"),
    rule("Suggestions"),
    perfData.tips.length ? h("div", { class: "col" }, perfData.tips.map((t) => h("div", { class: `note ${t.level === "warn" ? "warn" : ""}` }, icon(t.level === "warn" ? "alert" : "info"),
      h("div", { class: "grow" }, t.text), (t.apply || t.apply_config) ? h("button", { class: "btn xs", onclick: async () => {
        const r = await act("perf/apply", { id: t.id }, "Applied");
        if (r && r.restart_needed) toast("Restart to use it", "This setting takes effect when the server restarts.", "warn");
        renderPerf(body);
      } }, "Apply") : null))) : h("div", { class: "empty" }, "Nothing to suggest."),
    rule("Watchdog"),
    h("label", { class: "switch", style: { marginBottom: "8px" } }, h("input", { type: "checkbox", checked: (S.settings.watchdog || {}).enabled !== false, onchange: (e) => act("settings", { changes: { watchdog: { enabled: e.target.checked } } }) }), h("span", { class: "track" }), h("span", null, "Recover an overloaded server automatically")),
    h("label", { class: "switch", style: { marginBottom: "12px" } }, h("input", { type: "checkbox", checked: (S.settings.watchdog || {}).adaptive_view !== false, onchange: (e) => act("settings", { changes: { watchdog: { adaptive_view: e.target.checked } } }) }), h("span", { class: "track" }), h("span", null, "Lower view distance while memory is tight")),
    (wd.events || []).length ? h("div", { class: "list" }, [...wd.events].reverse().slice(0, 12).map((e) => h("div", { class: "item", style: { minHeight: "40px" } },
      icon(e.level === "critical" ? "alert" : e.level === "strained" ? "pulse" : "info"),
      h("div", { class: "main" }, h("div", { style: { fontSize: "15px", lineHeight: "20px" } }, e.msg), h("div", { class: "sub" }, ago(e.t)))))) : h("div", { class: "empty" }, "No incidents yet."),
    perfData.launch ? h("details", { style: { marginTop: "16px" } }, h("summary", { class: "label", style: { cursor: "pointer" } }, "Launch command"),
      h("pre", { class: "mono", style: { whiteSpace: "pre-wrap", wordBreak: "break-all", background: "var(--bg)", padding: "12px", borderRadius: "8px", border: "1px solid var(--line)" } }, perfData.launch.command)) : null);
}

// ============================================================================ mods & logs
async function renderMods(body) {
  let d;
  try { d = await api("mods"); } catch (e) { clear(body, h("div", { class: "empty" }, e.message)); return; }
  const b = d.bridge || {};
  let logs = { files: [] };
  try { logs = await api("logs"); } catch { /* ignore */ }
  const bridgeCard = h("div", { class: "card", style: { marginTop: "8px" } },
    h("div", { class: "row" }, icon("plug", "lg"), h("div", { class: "grow" }, h("div", { style: { fontWeight: 700, fontSize: "18px" } }, "OrbisFox Bridge"),
      h("div", { class: "muted" }, b.connected ? `Connected  ·  v${b.version}` : b.installed ? "Installed  ·  connects when the server runs" : "Not installed"))),
    h("p", { class: "muted", style: { fontSize: "15px", margin: "8px 0 12px" } }, "A small plugin that gives OrbisFox pings, kills, distance walked, per-world tick rates, the loaded-chunk map, and spots a stuck world within seconds."),
    b.installed ? (b.bundled ? h("button", { class: "btn sm", onclick: async () => { const r = await act("bridge/install", {}, "Plugin updated"); if (r && r.restart_needed) toast("Restart the server to load it", "", "warn"); renderMods(body); } }, icon("refresh", "sm"), "Reinstall") : null)
      : b.bundled ? h("button", { class: "btn primary sm", onclick: async () => { const r = await act("bridge/install", {}, "Plugin installed"); if (r && r.restart_needed) toast("Restart the server to load it", "", "warn"); renderMods(body); } }, icon("plug", "sm"), "Install plugin")
        : h("div", { class: "note" }, icon("info"), h("div", null, "The plugin isn't built in this copy of OrbisFox. Download OrbisFoxBridge.jar from the OrbisFox releases page into plugin/dist, or build it (see plugin/README.md).")));
  clear(body, bridgeCard,
    rule("Mods", h("button", { class: "btn ghost xs", onclick: () => act("open", { which: "mods" }) }, icon("folder", "sm"), "Open folder")),
    d.mods.length ? h("div", { class: "list" }, d.mods.map((m) => h("div", { class: "item" },
      h("label", { class: "switch" }, h("input", { type: "checkbox", checked: m.enabled, "aria-label": `${m.name} enabled`, onchange: async () => { const r = await act("mods/toggle", { file: m.file }); if (r && r.restart_needed) toast("Restart to apply", "Mods load when the server starts.", "warn"); renderMods(body); } }), h("span", { class: "track" })),
      h("div", { class: "main" }, h("div", { class: "name" }, m.name), h("div", { class: "sub" }, `${bytes(m.size)}  ·  ${dateTime(m.modified)}`))))) : h("div", { class: "empty" }, "No mods installed. Drop .jar or .zip mods into the mods folder."),
    rule("Server logs"),
    logs.files.length ? h("div", { class: "list" }, logs.files.slice(0, 15).map((f) => h("div", { class: "item clickable", onclick: () => viewLog(f.name) }, icon("terminal"),
      h("div", { class: "main" }, h("div", { class: "name mono", style: { fontSize: "13px" } }, f.name), h("div", { class: "sub" }, `${bytes(f.size)}  ·  ${ago(f.modified)}`))))) : h("div", { class: "empty" }, "No log files yet."));
}

async function viewLog(name) {
  let d;
  try { d = await api(`logs/read?name=${encodeURIComponent(name)}`); } catch (e) { toast("Can't open log", e.message, "bad"); return; }
  const pre = h("pre", { class: "mono", style: { margin: 0, padding: "16px", whiteSpace: "pre-wrap", wordBreak: "break-word", overflow: "auto", flex: 1, background: "var(--bg)" } }, d.text);
  const search = h("input", { class: "input sm", placeholder: "Find in log", style: { width: "260px" }, onkeydown: (e) => { if (e.key === "Enter") window.find && window.find(search.value); } });
  modal({ title: name, wide: true, body: h("div", { style: { display: "flex", flexDirection: "column", flex: 1, minHeight: 0 } },
    h("div", { class: "row", style: { padding: "8px 16px" } }, search, d.truncated ? h("span", { class: "muted" }, "Showing the last 400 KB") : null), pre) });
  setTimeout(() => { pre.scrollTop = pre.scrollHeight; }, 50);
}

// ============================================================================ settings
async function renderSettings(body) {
  let d;
  try { d = await api("settings"); } catch (e) { clear(body, h("div", { class: "empty" }, e.message)); return; }
  const s = d.settings;
  const inputs = [];
  const bind = (path, el, kind = "text") => { inputs.push({ path, el, kind }); return el; };
  const txt = (path, val, attrs = {}) => bind(path, h("input", { class: "input", value: val ?? "", ...attrs }));
  const numIn = (path, val, attrs = {}) => bind(path, h("input", { class: "input", type: "number", value: val ?? 0, ...attrs }), "number");
  const chk = (path, val, label, help) => h("label", { class: "switch", style: { marginBottom: "12px", alignItems: "flex-start" } }, bind(path, h("input", { type: "checkbox", checked: !!val }), "bool"), h("span", { class: "track", style: { marginTop: "2px" } }),
    h("span", null, label, help ? h("div", { class: "muted", style: { fontSize: "15px", lineHeight: "20px" } }, help) : null));
  const field = (label, el, help) => h("label", { class: "field" }, h("span", { class: "label" }, label), el, help ? h("span", { class: "help" }, help) : null);
  const lay = d.layout || {};
  const java = d.java || {};
  const dirInput = txt("server_dir", s.server_dir, { placeholder: "C:\\Hytale\\Server" });
  const restarts = bind("restarts.daily", h("input", { class: "input", value: (s.restarts.daily || []).join(", "), placeholder: "05:00, 17:00" }), "list");
  const lanOn = !!s.panel.lan;
  const pw = h("input", { class: "input", type: "password", placeholder: s.panel.has_password ? "Password is set (type to change)" : "At least 8 characters", autocomplete: "new-password" });
  clear(body,
    rule("Server"),
    field("Server folder", h("div", { class: "row" }, dirInput, window.orbis.local ? h("button", { class: "btn", onclick: async () => { const r = await act("pick-folder", { start: dirInput.value }); if (r && r.path) dirInput.value = r.path; } }, "Browse") : null),
      lay.problems && lay.problems.length ? h("span", { style: { color: "var(--amber-text)" } }, lay.problems.join(" ")) : `Found HytaleServer.jar${lay.assets ? " and Assets.zip" : ""}${lay.game_dir ? " (official layout)" : ""}.`),
    field("Java", txt("java", s.java, { placeholder: java.path ? `Automatic: ${java.path}` : "Automatic" }),
      java.path ? (java.version >= 25 ? `Using Java ${java.version}.` : h("span", { style: { color: "var(--amber-text)" } }, `Found Java ${java.version}; Hytale needs Java 25.`)) : h("span", { style: { color: "var(--amber-text)" } }, "No Java found. Install Java 25 (for example Eclipse Temurin 25).")),
    h("div", { class: "field-row" }, field("Memory min (GB)", numIn("ram_min_gb", s.ram_min_gb, { min: 1, max: 512, step: 0.5 })), field("Memory max (GB)", numIn("ram_max_gb", s.ram_max_gb, { min: 1, max: 512, step: 0.5 }))),
    h("p", { class: "help muted", style: { marginTop: "-8px", fontSize: "15px" } }, "Max is the Java heap (-Xmx). 6 GB suits up to 3 players; give 8 GB or more when players explore apart."),
    field("Address and port", txt("bind", s.bind, { placeholder: "0.0.0.0:5520" }), "Players connect to this UDP port (QUIC). Forward it on your router."),
    chk("exit_on_oom", s.exit_on_oom, "Exit when out of memory", "A server out of memory restarts cleanly instead of hanging."),
    chk("gc_log", s.gc_log, "Write a GC log", "Lets OrbisFox measure memory pressure. Tiny and rotated."),
    chk("aot_cache", s.aot_cache, "Use the AOT cache", "Faster boot using HytaleServer.aot."),
    field("Extra Java arguments", txt("jvm_args", s.jvm_args, { placeholder: "-XX:+UseZGC" })),
    field("Extra server arguments", txt("server_args", s.server_args, { placeholder: "--disable-sentry" })),
    rule("Starting and stopping"),
    chk("auto_start", s.auto_start, "Start the server when OrbisFox opens"),
    chk("auto_restart", s.auto_restart, "Restart the server after a crash", "Pauses after 3 crashes in 10 minutes."),
    window.orbis.local ? chk("start_with_windows", s.start_with_windows, "Start OrbisFox with Windows", "Runs in the background; open the window from the desktop shortcut.") : null,
    field("Daily restarts", restarts, "24-hour times, comma separated. Players are warned 10, 5 and 1 minutes before."),
    field("Stop timeout (seconds)", numIn("stop_timeout_s", s.stop_timeout_s, { min: 15, max: 600 }), "How long to wait for a clean stop before ending the process."),
    rule("Backups"),
    h("div", { class: "field-row" }, field("Back up every (minutes)", numIn("backups.every_minutes", s.backups.every_minutes, { min: 0 }), "0 = off"), field("Keep newest", numIn("backups.keep", s.backups.keep, { min: 1, max: 500 }))),
    field("Backup folder", txt("backups.dir", s.backups.dir, { placeholder: "Default: OrbisFox's data\\backups" })),
    chk("backups.before_update", s.backups.before_update, "Back up before installing updates"),
    chk("backups.include_mods", s.backups.include_mods, "Include the mods folder"),
    chk("backups.hytale_builtin", s.backups.hytale_builtin, "Also let the server back itself up", "The official --backup option. Applies at the next start."),
    field("Server backup interval (minutes)", numIn("backups.hytale_frequency", s.backups.hytale_frequency, { min: 5 })),
    rule("Watchdog"),
    chk("watchdog.enabled", s.watchdog.enabled, "Recover an overloaded server", "Restarts with a warning when memory, GC or world ticks stay critical."),
    chk("watchdog.adaptive_view", s.watchdog.adaptive_view, "Adaptive view distance", "Lowers view distance in steps of 2 (down to 6) while memory is tight, raises it back when calm."),
    chk("watchdog.warn_players", s.watchdog.warn_players, "Warn players 30 seconds before a recovery restart"),
    h("div", { class: "field-row" }, field("Critical heap %", numIn("watchdog.heap_percent", s.watchdog.heap_percent, { min: 70, max: 99 })), field("Critical GC time %", numIn("watchdog.gc_overhead_percent", s.watchdog.gc_overhead_percent, { min: 10, max: 90 }))),
    h("div", { class: "field-row" }, field("Sustained for (s)", numIn("watchdog.window_s", s.watchdog.window_s, { min: 30, max: 1800 })), field("World stuck after (s)", numIn("watchdog.unresponsive_s", s.watchdog.unresponsive_s, { min: 10, max: 600 }))),
    rule("Updates"),
    field("Check every (hours)", numIn("updates.check_every_hours", s.updates.check_every_hours, { min: 0 }), "0 = only when you press Check."),
    field("hytale-downloader path", txt("updates.downloader", s.updates.downloader, { placeholder: "Automatic" })),
    rule("Panel access"),
    chk("panel.lan", lanOn, "Allow other devices on my network", "Open OrbisFox from a phone or another PC. Needs a password. Takes effect after OrbisFox restarts."),
    field("Panel password", h("div", { class: "row" }, pw, h("button", { class: "btn", onclick: async () => { if (await act("settings/password", { password: pw.value }, "Password saved")) { pw.value = ""; } } }, "Set"))),
    field("Panel port", numIn("panel.port", s.panel.port, { min: 1024, max: 65535 })),
    h("div", { class: "row", style: { position: "sticky", bottom: "-24px", background: "rgba(16,26,28,0.98)", padding: "12px 0", borderTop: "1px solid var(--line)", marginTop: "16px" } },
      h("button", { class: "btn ghost sm", onclick: () => act("open", { which: "data" }) }, icon("folder", "sm"), "Data folder"),
      h("div", { class: "grow" }),
      h("button", { class: "btn primary", onclick: () => saveSettings(inputs, body) }, icon("save"), "Save")),
    h("p", { class: "muted", style: { fontSize: "14px" } }, `OrbisFox ${S.app.version}  ·  ${S.app.data_dir || ""}`));
}

async function saveSettings(inputs, body) {
  const changes = {};
  for (const { path, el, kind } of inputs) {
    let v = kind === "bool" ? el.checked : kind === "number" ? +el.value : kind === "list" ? el.value.split(/[,\s]+/).map((x) => x.trim()).filter(Boolean) : el.value.trim();
    const parts = path.split(".");
    let o = changes;
    for (const p of parts.slice(0, -1)) o = o[p] ||= {};
    o[parts[parts.length - 1]] = v;
  }
  const r = await act("settings", { changes });
  if (!r) return;
  toast("Settings saved", isAlive() ? "Java and server options apply at the next restart." : "");
  if (r.panel_restart_needed) toast("Restart OrbisFox for panel access changes", "Quit from the menu and open it again.", "warn", 9000);
  renderSettings(body);
}

// ============================================================================ registry
export const PANELS = [
  { id: "players", title: "Players", icon: "users", render: renderPlayers, events: ["players", "access"] },
  { id: "access", title: "Access", icon: "shield", render: renderAccess, events: ["access", "server"] },
  { id: "world", title: "World", icon: "sun", render: renderWorld, events: [] },
  { id: "backups", title: "Backups", icon: "archive", render: renderBackups, events: ["backups"] },
  { id: "updates", title: "Updates", icon: "download", render: renderUpdates, events: ["updates"] },
  { id: "stats", title: "Stats", icon: "trophy", render: renderStats, events: [], poll: 15000 },
  { id: "perf", title: "Performance", icon: "pulse", render: renderPerf, events: ["watchdog"], tick: 4000 },
  { id: "map", title: "Chunk map", icon: "map", overlay: true },
  { id: "mods", title: "Mods and logs", icon: "layers", render: renderMods, events: [] },
  "sep",
  { id: "settings", title: "Settings", icon: "gear", render: renderSettings, events: [] },
];

export function mountDock(dockEl, railEl, openMap) {
  const titleEl = h("h2", { class: "frame-title grow" });
  const bodyEl = h("div", { class: "dock-body" });
  const closeBtn = h("button", { class: "btn ghost icon sm", "aria-label": "Close panel", onclick: () => open(null) }, icon("x"));
  dockEl.querySelector(".frame-in").append(h("div", { class: "frame-head" }, titleEl, closeBtn), bodyEl);
  const app = document.querySelector(".app");
  let current = null, timer = null;
  const buttons = {};
  for (const p of PANELS) {
    if (p === "sep") { railEl.append(h("div", { class: "sep" })); continue; }
    const b = h("button", { "aria-label": p.title, onclick: () => (p.overlay ? openMap() : open(current === p.id ? null : p.id)) },
      icon(p.icon), h("span", { class: "tip" }, p.title), h("span", { class: "badge hidden" }));
    buttons[p.id] = b;
    railEl.append(b);
  }
  function open(id) {
    clearInterval(timer);
    current = id;
    try { localStorage.setItem("orbisfox-panel", id || ""); } catch { /* storage off */ }
    for (const [pid, b] of Object.entries(buttons)) b.setAttribute("aria-current", String(pid === id));
    app.classList.toggle("dock-closed", !id);
    if (!id) return;
    const p = PANELS.find((x) => x.id === id);
    titleEl.textContent = p.title;
    bodyEl.scrollTop = 0;
    p.render(bodyEl);
    if (p.poll) timer = setInterval(() => p.render(bodyEl), p.poll);
    if (p.tick) timer = setInterval(() => p.render(bodyEl, false), p.tick);
  }
  const rerender = (kind) => () => {
    const p = PANELS.find((x) => x.id === current);
    if (p && p.events && p.events.includes(kind)) {
      const y = bodyEl.scrollTop;
      p.render(bodyEl);
      bodyEl.scrollTop = y;
    }
  };
  for (const k of ["players", "access", "backups", "updates", "watchdog", "server"]) on(k, rerender(k));
  const badges = () => {
    const n = (S.players.online || []).length;
    setBadge(buttons.players, n ? String(n) : "");
    setBadge(buttons.updates, S.updates.available ? "1" : "", true);
    setBadge(buttons.perf, S.watchdog.state === "critical" || S.watchdog.state === "strained" ? "!" : "", true);
  };
  for (const k of ["snapshot", "players", "updates", "watchdog"]) on(k, badges);
  let saved = null;
  try { saved = localStorage.getItem("orbisfox-panel"); } catch { /* storage off */ }
  open(saved === null ? "players" : saved || null);
  return { open };
}

function setBadge(btn, text, hot) {
  const b = btn.querySelector(".badge");
  b.textContent = text;
  b.classList.toggle("hidden", !text);
  b.classList.toggle("hot", !!hot);
}
