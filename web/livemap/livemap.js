/* OrbisFox explored-world map viewer. Plain script, no libraries: it works from the OrbisFox panel,
   from the read-only network share, inside OBS, or uploaded to any static web host.
   Everything is drawn here in the browser; the server PC only hands out files. */
(function () {
  "use strict";
  var params = new URLSearchParams(location.search);
  var KEY = params.get("key") || "";
  var OVERLAY = params.get("overlay") === "1";
  var SHOW_PLAYERS = params.get("players") !== "0";
  var FOLLOW = (params.get("follow") || "").toLowerCase();
  var REFRESH = Math.max(15, +(params.get("refresh") || 60)) * 1000;
  var EMBED = params.get("embed") === "1";
  if (OVERLAY) document.body.classList.add("overlay");
  if (params.get("bg") === "transparent") document.body.classList.add("transparent");

  var canvas = document.getElementById("map");
  var ctx = canvas.getContext("2d");
  var cam = { x: 0, z: 0, scale: 0.25 };      // scale = screen pixels per block
  var world = params.get("world") || "";
  var index = null;                           // <world>/index.json
  var players = [];
  var loaded = null;                          // [[cx, cz], ...] for the overlay
  var fitted = false;
  var images = new Map();                     // url -> {img, ok}
  var queue = [];
  var inflight = 0;

  function url(path) { return path + (KEY ? (path.indexOf("?") < 0 ? "?" : "&") + "key=" + encodeURIComponent(KEY) : ""); }
  function getJSON(path) {
    return fetch(url(path), { cache: "no-cache" }).then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); });
  }

  // ---------------------------------------------------------------- admin extras (inside the OrbisFox panel)
  var panel = null;
  try { if (EMBED && window.parent !== window && window.parent.orbis) panel = window.parent.orbis; } catch (e) { panel = null; }

  // ---------------------------------------------------------------- data
  function loadWorlds() {
    return getJSON("worlds.json").then(function (d) {
      var ws = (d.worlds || []).filter(function (w) { return w.regions > 0; });
      var sel = document.getElementById("world");
      if (!ws.length) { showEmpty(true); return; }
      showEmpty(false);
      if (!world || !ws.some(function (w) { return w.name === world; })) {
        var def = ws.find(function (w) { return w.name === "default"; }) || ws.reduce(function (a, b) { return b.regions > a.regions ? b : a; });
        world = def.name;
      }
      sel.hidden = ws.length < 2;
      sel.innerHTML = "";
      ws.forEach(function (w) { var o = document.createElement("option"); o.value = w.name; o.textContent = w.name; o.selected = w.name === world; sel.appendChild(o); });
    }).catch(function () { showEmpty(true); });
  }

  function loadIndex() {
    if (!world) return Promise.resolve();
    return getJSON(encodeURIComponent(world) + "/index.json").then(function (d) {
      index = d;
      var n = (d.regions || []).length;
      var blocks = (d.region_chunks || 16) * (d.chunk_blocks || 32);
      var km2 = (n * blocks * blocks) / 1e6;
      document.getElementById("t-name").textContent = world === "default" ? "World map" : world;
      document.getElementById("t-sub").textContent = "Explored about " + (km2 >= 1 ? km2.toFixed(1) + " km²" : Math.round(km2 * 1e6).toLocaleString() + " blocks²") +
        (d.updated ? "  ·  updated " + ago(d.updated) : "");
      if (!fitted) { fitted = true; applyStart() || fit(); }
      draw();
    }).catch(function () {});
  }

  function loadPlayers() {
    if (!SHOW_PLAYERS) return;
    getJSON("players.json").then(function (d) {
      players = (d.players || []).filter(function (p) { return !p.world || p.world === world || !world; });
      if (FOLLOW) {
        var p = players.find(function (q) { return (q.name || "").toLowerCase() === FOLLOW; });
        if (p) { cam.x = p.x; cam.z = p.z; }
      }
      document.getElementById("lg-players").style.display = players.length ? "" : "none";
      draw();
    }).catch(function () { document.getElementById("lg-players").style.display = "none"; });
  }

  function loadLoaded() {
    if (!panel || !document.getElementById("loaded").checked) { loaded = null; document.getElementById("loaded-n").textContent = ""; draw(); return; }
    fetch("/api/map/chunks?world=" + encodeURIComponent(world === "default" ? "" : world), { headers: { "X-OrbisFox-Token": panel.token() } })
      .then(function (r) { return r.json().then(function (d) { if (!r.ok) throw new Error(d.error || r.status); return d; }); })
      .then(function (d) { loaded = d.chunks || []; document.getElementById("loaded-n").textContent = loaded.length.toLocaleString(); draw(); })
      .catch(function (e) { loaded = null; document.getElementById("loaded-n").textContent = "(" + e.message + ")"; draw(); });
  }

  // ---------------------------------------------------------------- camera
  function size() {
    var dpr = window.devicePixelRatio || 1;
    canvas.width = Math.round(innerWidth * dpr);
    canvas.height = Math.round(innerHeight * dpr);
    draw();
  }
  function toScreen(x, z) { return [(x - cam.x) * cam.scale + innerWidth / 2, (z - cam.z) * cam.scale + innerHeight / 2]; }
  function toWorld(sx, sy) { return [(sx - innerWidth / 2) / cam.scale + cam.x, (sy - innerHeight / 2) / cam.scale + cam.z]; }
  function regionBlocks() { return index ? (index.region_chunks || 16) * (index.chunk_blocks || 32) : 512; }

  function fit() {
    if (!index || !(index.regions || []).length) return;
    var rb = regionBlocks(), x0 = Infinity, z0 = Infinity, x1 = -Infinity, z1 = -Infinity;
    index.regions.forEach(function (r) { x0 = Math.min(x0, r[0] * rb); z0 = Math.min(z0, r[1] * rb); x1 = Math.max(x1, (r[0] + 1) * rb); z1 = Math.max(z1, (r[1] + 1) * rb); });
    cam.x = (x0 + x1) / 2; cam.z = (z0 + z1) / 2;
    cam.scale = clampScale(Math.min(innerWidth / (x1 - x0 + 128), innerHeight / (z1 - z0 + 128)));
    draw();
  }
  function applyStart() {
    if (!params.has("x") && !params.has("z") && !params.has("zoom")) return false;
    cam.x = +(params.get("x") || 0); cam.z = +(params.get("z") || 0);
    cam.scale = clampScale(+(params.get("zoom") || 0.5));
    return true;
  }
  function clampScale(s) { return Math.max(0.005, Math.min(16, s || 0.25)); }
  function zoomBy(f, sx, sy) {
    sx = sx == null ? innerWidth / 2 : sx; sy = sy == null ? innerHeight / 2 : sy;
    var w = toWorld(sx, sy);
    cam.scale = clampScale(cam.scale * f);
    var n = toWorld(sx, sy);
    cam.x += w[0] - n[0]; cam.z += w[1] - n[1];
    draw();
  }

  // ---------------------------------------------------------------- tiles
  function levelFor(scale) { return scale >= 0.6 ? 1 : scale >= 0.15 ? 4 : 16; }
  function tile(level, r) {
    var u = url(encodeURIComponent(world) + "/" + level + "/" + r[0] + "_" + r[1] + ".png?v=" + (r[2] || 0));
    var t = images.get(u);
    if (t) return t;
    t = { img: null, ok: false, u: u, r: r };
    images.set(u, t);
    queue.push(t);
    pump();
    if (images.size > 1500) {               // forget the oldest images
      var it = images.keys();
      for (var i = 0; i < 300; i++) images.delete(it.next().value);
    }
    return t;
  }
  function pump() {
    while (inflight < 6 && queue.length) {
      var best = 0, bd = Infinity, rb = regionBlocks();
      for (var i = 0; i < queue.length; i++) {
        var r = queue[i].r, d = Math.pow((r[0] + 0.5) * rb - cam.x, 2) + Math.pow((r[1] + 0.5) * rb - cam.z, 2);
        if (d < bd) { bd = d; best = i; }
      }
      var t = queue.splice(best, 1)[0];
      if (!visible(t.r)) { images.delete(t.u); continue; }
      inflight++;
      (function (t) {
        var img = new Image();
        img.onload = function () { t.img = img; t.ok = true; inflight--; pump(); schedule(); };
        img.onerror = function () { inflight--; pump(); };
        img.src = t.u;
      })(t);
    }
  }
  function visible(r) {
    var rb = regionBlocks(), a = toScreen(r[0] * rb, r[1] * rb), px = rb * cam.scale;
    return !(a[0] + px < -64 || a[1] + px < -64 || a[0] > innerWidth + 64 || a[1] > innerHeight + 64);
  }

  var pending = false;
  function schedule() { if (!pending) { pending = true; requestAnimationFrame(function () { pending = false; draw(); }); } }

  function draw() {
    var dpr = window.devicePixelRatio || 1;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, innerWidth, innerHeight);
    if (!document.body.classList.contains("transparent")) { ctx.fillStyle = "#0A1416"; ctx.fillRect(0, 0, innerWidth, innerHeight); }
    if (!index) return;
    var rb = regionBlocks(), px = rb * cam.scale, level = levelFor(cam.scale);
    ctx.imageSmoothingEnabled = cam.scale < 1;
    (index.regions || []).forEach(function (r) {
      if (!visible(r)) return;
      var a = toScreen(r[0] * rb, r[1] * rb);
      var t = tile(level, r);
      var img = t.ok ? t.img : null;
      if (!img) {                             // show a coarser tile while the sharp one loads
        [16, 4, 1].some(function (lv) {
          if (lv === level) return false;
          var o = images.get(url(encodeURIComponent(world) + "/" + lv + "/" + r[0] + "_" + r[1] + ".png?v=" + (r[2] || 0)));
          if (o && o.ok) { img = o.img; return true; }
          return false;
        });
      }
      if (img) ctx.drawImage(img, a[0], a[1], px + 0.5, px + 0.5);
      else { ctx.fillStyle = "rgba(27,42,44,0.6)"; ctx.fillRect(a[0], a[1], px, px); }
    });
    if (loaded && loaded.length) {
      var cs = index.chunk_blocks || 32, cp = cs * cam.scale;
      ctx.fillStyle = "rgba(77,196,216,0.28)";
      loaded.forEach(function (c) {
        var a = toScreen(c[0] * cs, c[1] * cs);
        if (a[0] + cp < 0 || a[1] + cp < 0 || a[0] > innerWidth || a[1] > innerHeight) return;
        ctx.fillRect(a[0], a[1], Math.max(1, cp), Math.max(1, cp));
      });
    }
    if (SHOW_PLAYERS) {
      ctx.font = "700 14px Genos, sans-serif";
      players.forEach(function (p) {
        var a = toScreen(p.x, p.z);
        hex(a[0], a[1], 7);
        var w = ctx.measureText(p.name).width;
        ctx.fillStyle = "rgba(5,11,12,0.85)";
        ctx.fillRect(a[0] + 11, a[1] - 10, w + 10, 20);
        ctx.fillStyle = "#E2D3C0";
        ctx.fillText(p.name, a[0] + 16, a[1] + 5);
      });
    }
  }
  function hex(x, y, r) {
    ctx.beginPath();
    for (var i = 0; i < 6; i++) { var a = Math.PI / 3 * i - Math.PI / 2; ctx[i ? "lineTo" : "moveTo"](x + r * Math.cos(a), y + r * Math.sin(a)); }
    ctx.closePath();
    ctx.fillStyle = "#D66623"; ctx.fill();
    ctx.lineWidth = 2; ctx.strokeStyle = "#050B0C"; ctx.stroke();
  }
  function ago(ts) {
    var s = Date.now() / 1000 - ts;
    return s < 90 ? "just now" : s < 3600 ? Math.round(s / 60) + " min ago" : s < 86400 ? Math.round(s / 3600) + " h ago" : Math.round(s / 86400) + " days ago";
  }
  function showEmpty(on) { document.getElementById("empty").hidden = !on || OVERLAY; }

  // ---------------------------------------------------------------- input
  var pointers = new Map(), pinch = null;
  canvas.addEventListener("pointerdown", function (e) { canvas.setPointerCapture(e.pointerId); pointers.set(e.pointerId, [e.clientX, e.clientY]); });
  canvas.addEventListener("pointermove", function (e) {
    var w = toWorld(e.clientX, e.clientY);
    document.getElementById("coords").textContent = "x " + Math.round(w[0]) + "  z " + Math.round(w[1]);
    if (!pointers.has(e.pointerId)) return;
    var p = pointers.get(e.pointerId);
    if (pointers.size === 2) {
      pointers.set(e.pointerId, [e.clientX, e.clientY]);
      var v = Array.from(pointers.values()), d = Math.hypot(v[0][0] - v[1][0], v[0][1] - v[1][1]);
      if (pinch) zoomBy(d / pinch, (v[0][0] + v[1][0]) / 2, (v[0][1] + v[1][1]) / 2);
      pinch = d;
      return;
    }
    FOLLOW = "";
    cam.x -= (e.clientX - p[0]) / cam.scale; cam.z -= (e.clientY - p[1]) / cam.scale;
    pointers.set(e.pointerId, [e.clientX, e.clientY]);
    draw(); pump();
  });
  function up(e) { pointers.delete(e.pointerId); pinch = null; }
  canvas.addEventListener("pointerup", up);
  canvas.addEventListener("pointercancel", up);
  canvas.addEventListener("pointerleave", function () { document.getElementById("coords").textContent = ""; });
  canvas.addEventListener("wheel", function (e) { e.preventDefault(); zoomBy(e.deltaY < 0 ? 1.25 : 0.8, e.clientX, e.clientY); pump(); }, { passive: false });
  canvas.addEventListener("dblclick", function (e) { zoomBy(2, e.clientX, e.clientY); pump(); });
  document.getElementById("zin").onclick = function () { zoomBy(1.5); pump(); };
  document.getElementById("zout").onclick = function () { zoomBy(1 / 1.5); pump(); };
  document.getElementById("zfit").onclick = function () { fit(); pump(); };
  document.getElementById("world").onchange = function (e) { world = e.target.value; index = null; fitted = false; images.clear(); queue = []; loadIndex(); loadPlayers(); };
  window.addEventListener("keydown", function (e) {
    var step = 120 / cam.scale;
    if (e.key === "+" || e.key === "=") zoomBy(1.4);
    else if (e.key === "-") zoomBy(1 / 1.4);
    else if (e.key === "ArrowLeft") { cam.x -= step; draw(); }
    else if (e.key === "ArrowRight") { cam.x += step; draw(); }
    else if (e.key === "ArrowUp") { cam.z -= step; draw(); }
    else if (e.key === "ArrowDown") { cam.z += step; draw(); }
    else return;
    pump();
  });
  if (panel) {
    document.getElementById("tog-loaded").hidden = false;
    document.getElementById("loaded").onchange = loadLoaded;
    setInterval(loadLoaded, 10000);
  }
  window.addEventListener("resize", size);

  // ---------------------------------------------------------------- memories (memories.json from OrbisFox)
  var mem = null, memFilter = "missing", memSearch = "", view = params.get("view") === "memories" ? "memories" : "map";
  var TIME = { day: "Daytime", night: "Night only" };

  function el(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
  function whereText(e) {
    if (e.where && e.where.length) return e.where.map(function (w) { return w.zone + (w.biomes.length ? ": " + w.biomes.join(", ") : ""); }).join("  ·  ");
    return e.placed ? "Dungeons, camps and other structures" : "No natural spawns (bosses, babies or special events)";
  }
  function loadMemories() {
    return getJSON("memories.json").then(function (d) {
      mem = d;
      document.getElementById("tabs").hidden = OVERLAY;
      if (view === "memories") renderMemories();
    }).catch(function () {
      mem = null;
      document.getElementById("tabs").hidden = true;
      if (view === "memories") setView("map");
    });
  }
  function setView(v) {
    view = v;
    document.getElementById("memories").hidden = v !== "memories";
    document.getElementById("tab-map").setAttribute("aria-selected", String(v === "map"));
    document.getElementById("tab-mem").setAttribute("aria-selected", String(v === "memories"));
    ["legend", "coords"].forEach(function (id) { document.getElementById(id).style.visibility = v === "map" ? "" : "hidden"; });
    document.querySelector(".zoom").style.visibility = v === "map" ? "" : "hidden";
    document.getElementById("empty").style.display = v === "map" ? "" : "none";
    if (v === "memories") renderMemories(); else draw();
  }
  function renderMemories() {
    if (!mem) return;
    var t = mem.totals;
    document.getElementById("m-restored").textContent = t.restored;
    document.getElementById("m-total").textContent = t.total;
    document.getElementById("m-bar").style.width = (t.total ? (t.restored / t.total) * 100 : 0) + "%";
    document.getElementById("m-note").textContent = t.carried + " more are being carried and still need restoring at the Heart of Orbis. " + t.missing + " haven't been found yet.";
    var counts = { missing: t.missing, carried: t.carried, restored: t.restored, all: t.total };
    Array.prototype.forEach.call(document.querySelectorAll("#m-filter button"), function (b) {
      b.setAttribute("aria-pressed", String(b.dataset.f === memFilter));
      b.querySelector("b").textContent = counts[b.dataset.f];
    });
    var q = memSearch.toLowerCase();
    var rows = mem.entries.filter(function (e) {
      return (memFilter === "all" || e.status === memFilter) && (!q || (e.name + " " + e.category + " " + whereText(e)).toLowerCase().indexOf(q) >= 0);
    });
    var list = document.getElementById("m-list");
    list.textContent = "";
    if (!rows.length) { list.appendChild(el("p", "muted", memFilter === "missing" && !q ? "Every memory has been found." : "Nothing matches.")); return; }
    var groups = {};
    rows.forEach(function (e) { (groups[e.category] = groups[e.category] || []).push(e); });
    Object.keys(groups).sort().forEach(function (cat) {
      var h = el("div", "mem-cat"); h.appendChild(el("span", "label", cat + "  ·  " + groups[cat].length)); list.appendChild(h);
      var card = el("div", "card");
      groups[cat].forEach(function (e) {
        var row = el("div", "mem-row");
        var hx = el("div", "hex " + e.status, e.status === "restored" ? "✓" : e.name.split(/[ _]/).map(function (w) { return w[0]; }).join("").slice(0, 2).toUpperCase());
        var main = el("div", "");
        var nm = el("div", "nm", e.name);
        if (e.unlock === "Kill") nm.appendChild(el("span", "tag kill", "Defeat it"));
        if (TIME[e.time]) nm.appendChild(el("span", "tag time", TIME[e.time]));
        if (e.status === "restored") nm.appendChild(el("span", "tag done", "Restored"));
        main.appendChild(nm);
        main.appendChild(el("div", "where", whereText(e)));
        if (e.carried_by && e.carried_by.length) {
          main.appendChild(el("div", "who", "Carried by " + e.carried_by.map(function (c) { return (c.name || "a player") + (c.where ? " (found in " + c.where + ")" : ""); }).join(", ")));
        }
        row.appendChild(hx); row.appendChild(main); card.appendChild(row);
      });
      list.appendChild(card);
    });
  }
  document.getElementById("tab-map").onclick = function () { setView("map"); };
  document.getElementById("tab-mem").onclick = function () { setView("memories"); };
  Array.prototype.forEach.call(document.querySelectorAll("#m-filter button"), function (b) { b.onclick = function () { memFilter = b.dataset.f; renderMemories(); }; });
  document.getElementById("m-search").oninput = function (e) { memSearch = e.target.value.trim(); renderMemories(); };
  if (params.get("filter")) memFilter = params.get("filter");

  // ---------------------------------------------------------------- start
  loadMemories().then(function () { if (view === "memories" && mem) setView("memories"); });
  setInterval(loadMemories, 60000);
  size();
  loadWorlds().then(loadIndex).then(loadPlayers);
  setInterval(function () { loadWorlds().then(loadIndex); }, REFRESH);
  if (SHOW_PLAYERS) setInterval(loadPlayers, 10000);
})();
