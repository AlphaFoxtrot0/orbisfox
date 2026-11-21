// Top bar: brand, server status, live vitals and the start/stop/restart controls.
// Also the notice banners at the top of the console.
import { S, on, isAlive } from "./store.js";
import { h, icon, act, menu, spark, bytes, duration, ago, clear, confirmBox, toast } from "./util.js";

let pill, vitals, controls, notices;
const STATE_LABEL = {
  stopped: "Stopped", starting: "Starting", running: "Running", stopping: "Stopping", crashed: "Crashed",
};

function vital(id, label, onclick, opt = false) {
  const el = h("button", { class: `vital ${opt ? "opt" : ""}`, "data-v": id, onclick },
    h("span", { class: "label" }, label), h("span", { class: "v" }, "–"), h("span", { class: "sp" }));
  return el;
}

export function mountTop(root, openPanel) {
  pill = h("div", { class: "status-pill", "data-state": "stopped", role: "status" },
    h("span", { class: "dot" }), h("span", { class: "s" }, "Stopped"), h("span", { class: "sub" }, ""));
  vitals = h("div", { class: "vitals" },
    vital("cpu", "CPU", () => openPanel("perf")),
    vital("mem", "Memory", () => openPanel("perf")),
    vital("tps", "Tick rate", () => openPanel("perf"), true),
    vital("players", "Players", () => openPanel("players")),
    vital("backup", "Last backup", () => openPanel("backups"), true));
  controls = h("div", { class: "controls" });
  root.append(
    h("div", { class: "brand" }, h("img", { src: "img/mark-256.png", alt: "" }),
      h("div", null, h("div", { class: "word" }, "Orbis", h("b", null, "Fox")), h("span", { class: "sub", id: "server-name" }, "Hytale server"))),
    pill, vitals, controls);
  renderControls();
  on("snapshot", () => { renderStatus(); renderControls(); renderVitals(); renderNotices(); });
  on("server", () => { renderStatus(); renderControls(); renderNotices(); });
  on("metrics", renderVitals);
  on("players", renderVitals);
  on("backups", renderVitals);
  on("updates", renderNotices);
  on("watchdog", renderNotices);
  on("connection", (ok) => { renderStatus(); if (!ok) renderNotices(); });
  setInterval(() => { renderStatus(); renderVitals(); if (S.server.pending) renderNotices(); }, 1000);
}

function renderStatus() {
  const sv = S.server;
  const st = S.connected ? sv.state || "stopped" : "offline";
  pill.dataset.state = sv.pending ? "restarting" : st;
  let label = STATE_LABEL[st] || "Not connected";
  let sub = "";
  if (sv.pending) {
    const left = Math.max(0, Math.round(sv.pending.at - Date.now() / 1000));
    label = sv.pending.action === "restart" ? "Restarting" : "Stopping";
    sub = `in ${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}`;
  } else if (st === "running" && sv.ready_at) sub = uptime(Date.now() / 1000 - sv.started_at);
  else if (st === "crashed" && sv.exit_code != null) sub = `exit ${sv.exit_code}`;
  pill.querySelector(".s").textContent = label;
  pill.querySelector(".sub").textContent = sub;
  const nm = document.getElementById("server-name");
  if (nm) nm.textContent = [sv.name || "Hytale server", sv.version ? `v${sv.version}` : ""].filter(Boolean).join("  ·  ");
}

// Whole minutes only, so the header doesn't change width every second.
function uptime(s) {
  if (s < 60) return "<1 min";
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  if (d) return `${d}d ${h}h`;
  if (h) return `${h}h ${m}m`;
  return `${m} min`;
}

function renderControls() {
  const st = S.server.state;
  const alive = isAlive();
  const btns = [];
  if (S.server.pending) {
    btns.push(h("button", { class: "btn", onclick: () => act("server/cancel", {}) }, icon("x"), h("span", { class: "lbl" }, "Cancel " + S.server.pending.action)));
  }
  if (!alive) {
    btns.push(h("button", { class: "btn cyan", disabled: !S.connected || !!S.server.orphan, onclick: () => act("server/start", {}) }, icon("play"), h("span", { class: "lbl" }, "Start")));
  } else {
    btns.push(h("div", { class: "btn-group" },
      h("button", { class: "btn", disabled: st === "stopping", onclick: stopFlow }, icon("stop"), h("span", { class: "lbl" }, "Stop")),
      h("button", { class: "btn icon", "aria-label": "More stop options", onclick: (e) => menu(e.currentTarget, [
        { label: "Stop now", icon: "stop", onclick: () => act("server/stop", { warn_seconds: 0 }) },
        { label: "Stop in 1 minute (warn players)", icon: "clock", onclick: () => act("server/stop", { warn_seconds: 60 }) },
        { label: "Stop in 5 minutes (warn players)", icon: "clock", onclick: () => act("server/stop", { warn_seconds: 300 }) },
        "-",
        { label: "Force stop (no save)", icon: "power", danger: true, onclick: async () => {
          if (await confirmBox("Force stop?", "This ends the server process immediately. Anything not saved in the last few minutes is lost. Use it only when the server is stuck.", "Force stop", true)) act("server/kill", {});
        } },
      ]) }, icon("down", "sm"))));
  }
  btns.push(h("div", { class: "btn-group" },
    h("button", { class: "btn primary glow", disabled: !S.connected || st === "stopping", onclick: restartFlow }, icon("restart"), h("span", { class: "lbl" }, "Restart")),
    h("button", { class: "btn primary icon", "aria-label": "More restart options", disabled: !S.connected, onclick: (e) => menu(e.currentTarget, [
      { label: "Restart now", icon: "restart", onclick: () => act("server/restart", { warn_seconds: 0 }) },
      { label: "Restart in 1 minute (warn players)", icon: "clock", onclick: () => act("server/restart", { warn_seconds: 60 }) },
      { label: "Restart in 5 minutes (warn players)", icon: "clock", onclick: () => act("server/restart", { warn_seconds: 300 }) },
    ]) }, icon("down", "sm"))));
  clear(controls, btns);
}

async function stopFlow() {
  const n = (S.players.online || []).length;
  if (n && !(await confirmBox("Stop the server?", `${n} player${n > 1 ? "s are" : " is"} online. They will be disconnected. Use the arrow next to Stop to warn them first.`, "Stop now", true))) return;
  act("server/stop", { warn_seconds: 0 });
}

async function restartFlow() {
  const n = (S.players.online || []).length;
  if (!isAlive()) { act("server/restart", {}); return; }
  if (n) {
    const ok = await confirmBox("Restart the server?", `${n} player${n > 1 ? "s are" : " is"} online. OrbisFox will warn them and restart in 30 seconds. Use the arrow next to Restart to restart immediately.`, "Restart in 30 s");
    if (ok) act("server/restart", { warn_seconds: 30 });
    return;
  }
  act("server/restart", { warn_seconds: 0 });
}

function setVital(id, value, sub, series, max, cls) {
  const el = vitals.querySelector(`[data-v="${id}"]`);
  if (!el) return;
  const v = el.querySelector(".v");
  v.replaceChildren(value);
  if (sub) v.append(h("small", null, sub));
  el.classList.toggle("warn", cls === "warn");
  el.classList.toggle("bad", cls === "bad");
  const sp = el.querySelector(".sp");
  if (series) sp.replaceChildren(spark(series, { max, color: cls === "bad" ? "var(--ember)" : cls === "warn" ? "var(--amber)" : "var(--cyan)" }));
  else sp.replaceChildren();
}

function renderVitals() {
  const m = S.metrics.latest || {};
  const hist = (S.metrics.history || []).slice(-60);
  const alive = isAlive();
  setVital("cpu", alive && m.cpu != null ? `${m.cpu.toFixed(0)}%` : "–", null, alive ? hist.map((x) => x.cpu) : null, 100,
    m.cpu > 85 ? "bad" : m.cpu > 60 ? "warn" : "");
  // Memory: live Java heap after GC when the GC log is on, else the process's working set.
  if (alive && m.heap_used != null && m.heap_max) {
    const pct = (m.heap_used / m.heap_max) * 100;
    setVital("mem", `${(m.heap_used / 1024).toFixed(1)}`, ` / ${(m.heap_max / 1024).toFixed(0)} GB`,
      hist.map((x) => (x.heap_used != null && x.heap_max ? (x.heap_used / x.heap_max) * 100 : null)), 100,
      pct > 90 ? "bad" : pct > 78 ? "warn" : "");
  } else if (alive && m.rss) {
    setVital("mem", bytes(m.rss, 1), null, hist.map((x) => x.rss), null, "");
  } else setVital("mem", "–");
  if (alive && m.tps != null) setVital("tps", m.tps.toFixed(0), " / 30", hist.map((x) => x.tps), 30, m.tps < 12 ? "bad" : m.tps < 22 ? "warn" : "");
  else setVital("tps", "–");
  const n = (S.players.online || []).length;
  setVital("players", String(n), S.server.max_players ? ` / ${S.server.max_players}` : null);
  const last = S.backups.last;
  const age = last ? Date.now() / 1000 - last : null;
  setVital("backup", last ? ago(last).replace(" ago", "") : "never", null, null, null, !last || age > 86400 ? "warn" : "");
}

// ------------------------------------------------------------------------- notices
function notice(level, ico, title, body, actions = []) {
  return h("div", { class: `note ${level}`, style: { margin: "12px 16px 0" } }, icon(ico),
    h("div", { class: "grow" }, h("b", null, title), body ? h("div", { class: "muted" }, body) : null),
    actions.length ? h("div", { class: "row", style: { flex: "none" } }, actions) : null);
}

export function renderNotices() {
  notices = notices || document.getElementById("notices");
  if (!notices) return;
  const sv = S.server, out = [];
  if (!S.connected) {
    out.push(notice("warn", "alert", "Not connected to OrbisFox", "Reconnecting... If this lasts, OrbisFox may have been closed on the server PC."));
  } else {
    if (sv.orphan) {
      out.push(notice("bad", "alert", "A server from an earlier OrbisFox session is still running",
        `Process ${sv.orphan.pid}. OrbisFox can't read its console. Stop it to take over (unsaved progress since its last save is lost).`,
        [h("button", { class: "btn sm danger", onclick: () => act("server/kill-orphan", {}) }, "Stop it")]));
    }
    if (sv.problems && sv.problems.length) {
      out.push(notice("warn", "folder", "Server folder needed", sv.problems.join(" "),
        [h("button", { class: "btn sm primary", onclick: () => window.orbis.openPanel("settings") }, "Open settings")]));
    }
    if (sv.last_error) {
      out.push(notice("bad", "alert", "The server could not start", sv.last_error,
        (sv.other_servers || []).length ? [h("button", { class: "btn sm danger", onclick: async () => {
          if (await confirmBox("Start anyway?", "Only do this if the other process is a different Hytale server (its own folder and port). Two servers on the same world can damage it.", "Start anyway", true)) act("server/start", { force: true });
        } }, "Start anyway")] : []));
    }
    const vt = sv.vetoes || {};
    if (sv.pending && sv.pending.kind) {
      const left = Math.max(0, Math.round(sv.pending.at - Date.now() / 1000));
      const what = { daily: "Scheduled restart", watchdog: "Recovery restart", update: "Update install" }[sv.pending.kind] || "Automatic restart";
      out.push(notice("warn", "clock", `${what} in ${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}`,
        sv.pending.vetoable ? (vt.bridge ? "Operators online were told they can type /veto in game to cancel it." : "Operators could cancel it with /veto, but that needs the OrbisFox Bridge plugin.") : "",
        [h("button", { class: "btn sm", onclick: () => act("server/cancel", {}) }, "Cancel it")]));
    } else if (vt.active && vt.active.kind === "update") {
      out.push(notice("warn", "download", "Automatic update install counting down", "Operators online can type /veto in game to cancel it."));
    }
    for (const [kind, secs] of Object.entries(vt.cooldowns || {})) {
      const what = { daily: "scheduled restart", watchdog: "recovery restart", update: "automatic update" }[kind] || kind;
      const last = (vt.history || []).filter((x) => x.kind === kind).pop();
      out.push(notice("info", "shield", `${last ? last.player : "An operator"} cancelled the ${what}`,
        `OrbisFox will try again in about ${Math.max(1, Math.round(secs / 60))} minute${secs > 90 ? "s" : ""}.`));
    }
    if (sv.auth_prompt && (sv.auth_prompt.url || sv.auth_prompt.code)) {
      const url = sv.auth_prompt.url || "https://accounts.hytale.com/device";
      out.push(notice("warn", "key", "Sign the server in to Hytale",
        h("span", null, "Open ", h("a", { href: url, target: "_blank", rel: "noopener" }, url),
          sv.auth_prompt.code ? [" and enter code ", h("b", { class: "mono", style: { fontSize: "16px", color: "var(--ink)" } }, sv.auth_prompt.code)] : null, "."),
        [h("button", { class: "btn sm", onclick: () => { navigator.clipboard.writeText(sv.auth_prompt.code || url); toast("Copied"); } }, icon("copy", "sm"), "Copy")]));
    } else if (sv.auth_needed && sv.state === "running") {
      out.push(notice("warn", "key", "The server isn't signed in to Hytale", "Players can't join until it is. OrbisFox will show you a code to enter.",
        [h("button", { class: "btn sm primary", onclick: () => act("auth/login", {}) }, "Sign in"),
          h("button", { class: "btn sm", title: "Keeps the sign-in across restarts", onclick: () => act("auth/persist", {}, "The server will remember its sign-in") }, "Remember sign-in")]));
    }
    const wd = S.watchdog || {};
    if (wd.state === "critical" || wd.state === "strained") {
      out.push(notice(wd.state === "critical" ? "bad" : "warn", "pulse",
        wd.state === "critical" ? "The server is overloaded" : "The server is under strain",
        (wd.reasons || []).join(". ") + (wd.state === "critical" ? ". OrbisFox will recover it if this continues." : "."),
        [h("button", { class: "btn sm", onclick: () => window.orbis.openPanel("perf") }, "Details")]));
    }
    const u = S.updates || {};
    if (u.available && !u.busy) {
      out.push(notice("info", "download", `Server update available: ${u.latest.version}`, `You have ${u.installed}.`,
        [h("button", { class: "btn sm cyan", onclick: () => window.orbis.openPanel("updates") }, "See update")]));
    }
    if (u.busy) {
      const step = (u.steps || []).find((s) => s.state === "active");
      out.push(notice("info", "download", "Installing the server update", step ? `${step.label}...` : "Working...",
        [h("button", { class: "btn sm", onclick: () => window.orbis.openPanel("updates") }, "Progress")]));
    }
  }
  clear(notices, out);
}
