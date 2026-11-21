// Live state from the OrbisFox backend (one Server-Sent Events stream) and a tiny pub/sub.
import { getToken, toast } from "./util.js";

export const S = {
  connected: false,
  app: {}, settings: {}, server: {}, console: [], players: { online: [] },
  metrics: { latest: {}, history: [] }, watchdog: {}, backups: { list: [], builtin: [] },
  updates: {}, access: { whitelist: { entries: [] }, ops: [], bans: [] }, catalog: [], gc: {}, appupdate: {},
};
let seenVersion = null;

const subs = {};
export function on(kind, fn) { (subs[kind] ||= []).push(fn); return () => { subs[kind] = subs[kind].filter((f) => f !== fn); }; }
export function emit(kind, data) { for (const fn of subs[kind] || []) { try { fn(data); } catch (e) { console.error(e); } } }

let es = null;
let retry = 1000;

export function connect() {
  if (es) es.close();
  es = new EventSource(`/api/events?token=${encodeURIComponent(getToken() || "")}`);
  es.addEventListener("snapshot", (e) => {
    const d = JSON.parse(e.data);
    Object.assign(S, {
      app: d.app, settings: d.settings, server: d.server, console: d.console, players: d.players,
      metrics: d.metrics, watchdog: d.watchdog, backups: d.backups, updates: d.updates, access: d.access,
      catalog: d.catalog, appupdate: d.appupdate || {},
    });
    if (seenVersion && d.app.version !== seenVersion) toast(`OrbisFox updated to ${d.app.version}`, "Your settings, backups and stats came along.", "info", 10000);
    seenVersion = d.app.version;
    S.connected = true;
    retry = 1000;
    emit("snapshot", d);
    emit("connection", true);
  });
  es.addEventListener("line", (e) => {
    const ln = JSON.parse(e.data);
    S.console.push(ln);
    if (S.console.length > 6000) S.console.splice(0, S.console.length - 5000);
    emit("line", ln);
  });
  es.addEventListener("server", (e) => { S.server = JSON.parse(e.data); emit("server", S.server); });
  es.addEventListener("players", (e) => { S.players = JSON.parse(e.data); emit("players", S.players); });
  es.addEventListener("metrics", (e) => {
    const d = JSON.parse(e.data);
    S.metrics.latest = d.m;
    S.metrics.history.push(d.m);
    if (S.metrics.history.length > 450) S.metrics.history.shift();
    S.gc = d.gc;
    if (S.watchdog.state !== d.wd) { S.watchdog.state = d.wd; emit("watchdog", S.watchdog); }
    emit("metrics", d);
  });
  es.addEventListener("backups", (e) => { S.backups = JSON.parse(e.data); emit("backups", S.backups); });
  es.addEventListener("updates", (e) => { S.updates = JSON.parse(e.data); emit("updates", S.updates); });
  es.addEventListener("access", (e) => { S.access = JSON.parse(e.data); emit("access", S.access); });
  es.addEventListener("watchdog", (e) => { S.watchdog = JSON.parse(e.data); emit("watchdog", S.watchdog); });
  es.addEventListener("appupdate", (e) => { S.appupdate = JSON.parse(e.data); emit("appupdate", S.appupdate); });
  es.addEventListener("settings", (e) => { S.settings = JSON.parse(e.data); emit("settings", S.settings); });
  es.addEventListener("toast", (e) => { const t = JSON.parse(e.data); toast(t.title, t.body, t.level, t.ms); });
  es.onerror = () => {
    if (S.connected) { S.connected = false; emit("connection", false); }
    es.close();
    setTimeout(async () => {
      // OrbisFox may have restarted with a new token.
      if (reauth) { try { await reauth(); } catch { /* try again later */ } }
      connect();
    }, retry);
    retry = Math.min(retry * 2, 10000);
  };
}

let reauth = null;
export function setReauth(fn) { reauth = fn; }

export const isRunning = () => S.server.state === "running";
export const isAlive = () => ["starting", "running", "stopping"].includes(S.server.state);
