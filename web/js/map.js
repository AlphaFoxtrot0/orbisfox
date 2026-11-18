// Chunk map: every loaded chunk, drawn with the server's own map imagery, grouped into areas.
import { S } from "./store.js";
import { h, icon, api, act, modal, num, clear, toast, getToken } from "./util.js";

const TILE_CONCURRENCY = 8;

export function openMap() {
  const canvas = h("canvas");
  const coords = h("div", { class: "map-coords" });
  const view = h("div", { class: "map-view", tabindex: "0", "aria-label": "Chunk map. Drag to move, scroll to zoom." }, canvas, coords);
  const side = h("div", { class: "map-side" });
  const worldSel = h("select", { class: "input sm", style: { width: "auto" }, "aria-label": "World" }, h("option", { value: "" }, "Default world"));
  const zoom = h("div", { class: "map-zoom" },
    h("button", { class: "btn icon sm", "aria-label": "Zoom in", onclick: () => zoomBy(1.5) }, icon("zoomin")),
    h("button", { class: "btn icon sm", "aria-label": "Zoom out", onclick: () => zoomBy(1 / 1.5) }, icon("zoomout")),
    h("button", { class: "btn icon sm", "aria-label": "Fit everything", onclick: () => fit() }, icon("locate")));
  const hud = h("div", { class: "map-hud" },
    h("span", { class: "chip" }, h("span", { class: "legend-sw", style: { background: "#62904A" } }), "Loaded"),
    h("span", { class: "chip" }, h("span", { class: "legend-sw", style: { background: "repeating-linear-gradient(45deg,#E8B029 0 3px,transparent 3px 6px)", border: "1px solid #E8B029" } }), "Generating"),
    h("span", { class: "chip" }, h("span", { class: "legend-sw", style: { background: "#D66623", clipPath: "polygon(50% 0,100% 25%,100% 75%,50% 100%,0 75%,0 25%)" } }), "Player"));
  view.append(zoom, hud);
  const shell = h("div", { class: "map-shell" }, view, side);
  const head = h("div", { class: "row", style: { padding: "8px 16px", borderBottom: "1px solid var(--line)" } }, worldSel, h("span", { class: "muted", id: "map-src" }, ""), h("div", { class: "grow" }),
    h("button", { class: "btn sm", onclick: () => act("chunks/unload", {}, "Asked the server to unload unused chunks") }, icon("layers", "sm"), "Unload idle chunks"));
  const body = h("div", { style: { display: "flex", flexDirection: "column", flex: 1, minHeight: 0 } }, head, shell);

  let data = null, timer = null, alive = true;
  let set = new Set(), areas = [];
  const cam = { x: 0, z: 0, scale: 0.6 };   // scale = screen px per block
  let fitted = false;
  const tiles = new Map();
  let loading = 0;
  const queue = [];

  const m = modal({ title: "Chunk map", wide: true, body, onClose: () => { alive = false; clearInterval(timer); ro.disconnect(); } });

  function size() {
    const r = view.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.max(1, Math.round(r.width * dpr));
    canvas.height = Math.max(1, Math.round(r.height * dpr));
    draw();
  }
  const ro = new ResizeObserver(size);
  ro.observe(view);

  function toScreen(x, z) {
    const W = canvas.width / (window.devicePixelRatio || 1), H = canvas.height / (window.devicePixelRatio || 1);
    return [(x - cam.x) * cam.scale + W / 2, (z - cam.z) * cam.scale + H / 2];
  }
  function toWorld(sx, sy) {
    const W = canvas.width / (window.devicePixelRatio || 1), H = canvas.height / (window.devicePixelRatio || 1);
    return [(sx - W / 2) / cam.scale + cam.x, (sy - H / 2) / cam.scale + cam.z];
  }

  function tileFor(cx, cz) {
    if (!data || data.source !== "bridge") return null;
    const key = `${data.world}:${cx}:${cz}`;
    let t = tiles.get(key);
    if (t) return t;
    t = { img: null, state: "queued" };
    tiles.set(key, t);
    queue.push([key, cx, cz]);
    pump();
    return t;
  }
  function pump() {
    while (loading < TILE_CONCURRENCY && queue.length) {
      // nearest to the middle of the view first
      const cs = (data && data.chunk_size) || 32;
      let best = 0, bd = Infinity;
      for (let i = 0; i < queue.length; i++) {
        const d = (queue[i][1] * cs - cam.x) ** 2 + (queue[i][2] * cs - cam.z) ** 2;
        if (d < bd) { bd = d; best = i; }
      }
      const [key, cx, cz] = queue.splice(best, 1)[0];
      const t = tiles.get(key);
      if (!t || !alive) continue;
      loading++;
      const img = new Image();
      img.onload = () => { t.img = img; t.state = "ok"; loading--; pump(); schedule(); };
      img.onerror = () => { t.state = "fail"; loading--; pump(); };
      img.src = `/api/map/tile?world=${encodeURIComponent(data.world || "")}&x=${cx}&z=${cz}&token=${encodeURIComponent(getToken() || "")}`;
    }
  }
  let rafPending = false;
  function schedule() { if (!rafPending) { rafPending = true; requestAnimationFrame(() => { rafPending = false; draw(); }); } }

  function draw() {
    const ctx = canvas.getContext("2d");
    const dpr = window.devicePixelRatio || 1;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const W = canvas.width / dpr, H = canvas.height / dpr;
    ctx.fillStyle = "#0A1416";
    ctx.fillRect(0, 0, W, H);
    if (!data) return;
    const cs = data.chunk_size || 32;
    const px = cs * cam.scale;
    // chunk grid when zoomed in
    if (px >= 14) {
      ctx.strokeStyle = "rgba(47,68,70,0.35)";
      ctx.lineWidth = 1;
      const [wx0, wz0] = toWorld(0, 0), [wx1, wz1] = toWorld(W, H);
      ctx.beginPath();
      for (let x = Math.floor(wx0 / cs) * cs; x <= wx1; x += cs) { const [sx] = toScreen(x, 0); ctx.moveTo(Math.round(sx) + 0.5, 0); ctx.lineTo(Math.round(sx) + 0.5, H); }
      for (let z = Math.floor(wz0 / cs) * cs; z <= wz1; z += cs) { const [, sy] = toScreen(0, z); ctx.moveTo(0, Math.round(sy) + 0.5); ctx.lineTo(W, Math.round(sy) + 0.5); }
      ctx.stroke();
    }
    const showTiles = data.source === "bridge" && px >= 5;
    ctx.imageSmoothingEnabled = px < 32;
    for (const [cx, cz, st] of data.chunks) {
      const [sx, sy] = toScreen(cx * cs, cz * cs);
      if (sx + px < 0 || sy + px < 0 || sx > W || sy > H) continue;
      const t = showTiles && st !== 1 ? tileFor(cx, cz) : null;
      if (t && t.img) ctx.drawImage(t.img, sx, sy, px + 0.6, px + 0.6);
      else { ctx.fillStyle = st === 1 ? "rgba(232,176,41,0.18)" : "#3E6B48"; ctx.fillRect(sx, sy, px + 0.6, px + 0.6); }
      if (st === 1) {
        ctx.strokeStyle = "rgba(232,176,41,0.8)";
        ctx.lineWidth = 1;
        ctx.strokeRect(sx + 0.5, sy + 0.5, px - 1, px - 1);
        if (px >= 10) {
          ctx.beginPath();
          for (let i = -px; i < px; i += 6) { ctx.moveTo(sx + Math.max(0, i), sy + Math.max(0, -i)); ctx.lineTo(sx + Math.min(px, i + px), sy + Math.min(px, px - i)); }
          ctx.stroke();
        }
      }
    }
    // area outlines: edge of the loaded set
    if (data.chunks.length && px >= 2) {
      ctx.strokeStyle = "rgba(77,196,216,0.9)";
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      for (const [cx, cz] of data.chunks) {
        const [sx, sy] = toScreen(cx * cs, cz * cs);
        if (sx + px < 0 || sy + px < 0 || sx > W || sy > H) continue;
        if (!set.has(`${cx},${cz - 1}`)) { ctx.moveTo(sx, sy); ctx.lineTo(sx + px, sy); }
        if (!set.has(`${cx},${cz + 1}`)) { ctx.moveTo(sx, sy + px); ctx.lineTo(sx + px, sy + px); }
        if (!set.has(`${cx - 1},${cz}`)) { ctx.moveTo(sx, sy); ctx.lineTo(sx, sy + px); }
        if (!set.has(`${cx + 1},${cz}`)) { ctx.moveTo(sx + px, sy); ctx.lineTo(sx + px, sy + px); }
      }
      ctx.stroke();
    }
    // estimate mode: view-distance circles
    if (data.source === "estimate") {
      for (const p of data.players || []) {
        const [sx, sy] = toScreen(p.x, p.z);
        ctx.beginPath();
        ctx.arc(sx, sy, (data.radius || 12) * cs * cam.scale, 0, Math.PI * 2);
        ctx.fillStyle = "rgba(62,107,72,0.45)";
        ctx.fill();
        ctx.strokeStyle = "rgba(77,196,216,0.9)";
        ctx.stroke();
      }
    }
    // spawn
    if (data.spawn) {
      const [sx, sy] = toScreen(data.spawn.x, data.spawn.z);
      ctx.save();
      ctx.translate(sx, sy);
      ctx.rotate(Math.PI / 4);
      ctx.fillStyle = "#E8B029";
      ctx.fillRect(-5, -5, 10, 10);
      ctx.restore();
      ctx.font = "600 13px Genos, sans-serif";
      ctx.fillStyle = "#E8B029";
      ctx.fillText("Spawn", sx + 10, sy + 4);
    }
    // players
    ctx.font = "700 14px Genos, sans-serif";
    for (const p of data.players || []) {
      const [sx, sy] = toScreen(p.x, p.z);
      if (p.yaw != null) {
        const a = (p.yaw * Math.PI) / 180;
        ctx.strokeStyle = "rgba(214,102,35,0.9)";
        ctx.lineWidth = 2;
        ctx.beginPath(); ctx.moveTo(sx, sy); ctx.lineTo(sx + Math.cos(a) * 16, sy + Math.sin(a) * 16); ctx.stroke();
      }
      hex(ctx, sx, sy, 8, "#D66623", "#050B0C");
      const label = p.name + (p.stale ? " (last saved)" : "");
      const w = ctx.measureText(label).width;
      ctx.fillStyle = "rgba(5,11,12,0.85)";
      ctx.fillRect(sx + 12, sy - 10, w + 10, 20);
      ctx.fillStyle = "#E2D3C0";
      ctx.fillText(label, sx + 17, sy + 5);
    }
    // area labels
    if (px < 16) {
      ctx.font = "700 15px Genos, sans-serif";
      for (const a of areas) {
        const [sx, sy] = toScreen((a.cx + 0.5) * cs, (a.cz + 0.5) * cs);
        const t = `${num(a.n)} chunks`;
        const w = ctx.measureText(t).width;
        ctx.fillStyle = "rgba(5,11,12,0.8)";
        ctx.fillRect(sx - w / 2 - 6, sy - 26, w + 12, 20);
        ctx.fillStyle = "#4DC4D8";
        ctx.fillText(t, sx - w / 2, sy - 11);
      }
    }
  }

  function hex(ctx, x, y, r, fill, stroke) {
    ctx.beginPath();
    for (let i = 0; i < 6; i++) { const a = Math.PI / 3 * i - Math.PI / 2; ctx[i ? "lineTo" : "moveTo"](x + r * Math.cos(a), y + r * Math.sin(a)); }
    ctx.closePath();
    ctx.fillStyle = fill; ctx.fill();
    ctx.lineWidth = 2; ctx.strokeStyle = stroke; ctx.stroke();
  }

  // ---- areas (connected groups of loaded chunks)
  function computeAreas() {
    set = new Set(data.chunks.map(([x, z]) => `${x},${z}`));
    const seen = new Set();
    areas = [];
    for (const [x0, z0] of data.chunks) {
      const k0 = `${x0},${z0}`;
      if (seen.has(k0)) continue;
      const stack = [[x0, z0]];
      seen.add(k0);
      let n = 0, sx = 0, sz = 0;
      while (stack.length) {
        const [x, z] = stack.pop();
        n++; sx += x; sz += z;
        for (let dx = -1; dx <= 1; dx++) for (let dz = -1; dz <= 1; dz++) {
          const k = `${x + dx},${z + dz}`;
          if (!seen.has(k) && set.has(k)) { seen.add(k); stack.push([x + dx, z + dz]); }
        }
      }
      areas.push({ n, cx: sx / n, cz: sz / n });
    }
    const cs = data.chunk_size || 32;
    for (const a of areas) {
      a.players = (data.players || []).filter((p) => Math.hypot(p.x / cs - a.cx, p.z / cs - a.cz) < Math.sqrt(a.n) * 0.8 + 2).map((p) => p.name);
    }
    areas.sort((a, b) => b.n - a.n);
  }

  function renderSide() {
    if (!data) return;
    const total = data.chunks.length;
    const gen = data.chunks.filter((c) => c[2] === 1).length;
    const isBridge = data.source === "bridge";
    clear(side,
      h("div", { class: "stat-row" },
        h("div", { class: "stat" }, h("div", { class: "label" }, "Loaded chunks"), h("div", { class: "v" }, isBridge ? num(total) : "–")),
        h("div", { class: "stat" }, h("div", { class: "label" }, "Separate areas"), h("div", { class: "v" }, isBridge ? String(areas.length) : String((data.players || []).length)))),
      gen ? h("p", { class: "muted", style: { fontSize: "15px" } }, `${num(gen)} chunks are being generated right now.`) : null,
      isBridge ? null : h("div", { class: "note", style: { marginTop: "12px" } }, icon("plug"), h("div", null,
        h("b", null, "Estimated view. "), `Without the OrbisFox Bridge plugin OrbisFox can't see which chunks are loaded. Circles show each player's view distance (${data.radius} chunks).`)),
      h("div", { class: "rule" }, h("span", { class: "gem" }), h("span", { class: "label" }, isBridge ? "Areas" : "Players")),
      isBridge ? (areas.length ? areas.slice(0, 30).map((a, i) => h("div", { class: "cluster", tabindex: "0", onclick: () => center(a.cx * (data.chunk_size || 32), a.cz * (data.chunk_size || 32)) },
        h("div", { class: "n num" }, num(a.n)),
        h("div", { class: "grow" }, h("div", { style: { fontWeight: 600 } }, a.players.length ? a.players.join(", ") : i === 0 && data.spawn ? "Spawn area" : "Nobody nearby"),
          h("div", { class: "muted", style: { fontSize: "14px" } }, `around ${Math.round(a.cx * 32)}, ${Math.round(a.cz * 32)}`)))) : h("div", { class: "empty" }, "No chunks loaded."))
        : (data.players || []).map((p) => h("div", { class: "cluster", onclick: () => center(p.x, p.z) }, icon("users"), h("div", { class: "grow" }, h("b", null, p.name), h("div", { class: "muted", style: { fontSize: "14px" } }, `${Math.round(p.x)}, ${Math.round(p.z)}`)))),
      h("div", { class: "rule" }, h("span", { class: "gem" }), h("span", { class: "label" }, "Why this matters")),
      h("p", { class: "muted", style: { fontSize: "15px", lineHeight: "21px", margin: 0 } },
        "Every loaded chunk lives in the server's memory. Players together share one area; players exploring apart each load their own, and flying into new terrain generates chunks fast. ",
        "When memory runs short the server spends its time collecting garbage and stalls. Lower the view distance, give the server more memory, or let OrbisFox's watchdog do it for you."),
      h("div", { class: "row wrap", style: { marginTop: "12px" } },
        h("button", { class: "btn sm", onclick: () => { m.close(); window.orbis.openPanel("world"); } }, "View distance"),
        h("button", { class: "btn sm", onclick: () => { m.close(); window.orbis.openPanel("perf"); } }, "Performance")));
  }

  function fit() {
    if (!data) return;
    const cs = data.chunk_size || 32;
    const pts = data.chunks.length ? data.chunks.map(([x, z]) => [x * cs, z * cs]) : (data.players || []).map((p) => [p.x, p.z]);
    if (!pts.length) { cam.x = 0; cam.z = 0; cam.scale = 0.5; draw(); return; }
    let x0 = Infinity, z0 = Infinity, x1 = -Infinity, z1 = -Infinity;
    for (const [x, z] of pts) { x0 = Math.min(x0, x); z0 = Math.min(z0, z); x1 = Math.max(x1, x + cs); z1 = Math.max(z1, z + cs); }
    if (data.source === "estimate") { const r = (data.radius || 12) * cs; x0 -= r; z0 -= r; x1 += r; z1 += r; }
    const r = view.getBoundingClientRect();
    cam.x = (x0 + x1) / 2; cam.z = (z0 + z1) / 2;
    cam.scale = Math.max(0.02, Math.min(8, Math.min(r.width / (x1 - x0 + 64), r.height / (z1 - z0 + 64))));
    draw();
  }
  function center(x, z) { cam.x = x; cam.z = z; cam.scale = Math.max(cam.scale, 0.8); draw(); }
  function zoomBy(f, sx, sy) {
    const r = view.getBoundingClientRect();
    sx = sx ?? r.width / 2; sy = sy ?? r.height / 2;
    const [wx, wz] = toWorld(sx, sy);
    cam.scale = Math.max(0.02, Math.min(12, cam.scale * f));
    const [nx, nz] = toWorld(sx, sy);
    cam.x += wx - nx; cam.z += wz - nz;
    draw();
  }

  // ---- input
  const pointers = new Map();
  let pinch = null;
  view.addEventListener("pointerdown", (e) => { view.setPointerCapture(e.pointerId); pointers.set(e.pointerId, [e.offsetX, e.offsetY]); });
  view.addEventListener("pointermove", (e) => {
    const [wx, wz] = toWorld(e.offsetX, e.offsetY);
    const cs = (data && data.chunk_size) || 32;
    coords.textContent = `x ${Math.round(wx)}  z ${Math.round(wz)}  ·  chunk ${Math.floor(wx / cs)}, ${Math.floor(wz / cs)}`;
    if (!pointers.has(e.pointerId)) return;
    const [px, py] = pointers.get(e.pointerId);
    if (pointers.size === 2) {
      pointers.set(e.pointerId, [e.offsetX, e.offsetY]);
      const [a, b] = [...pointers.values()];
      const d = Math.hypot(a[0] - b[0], a[1] - b[1]);
      if (pinch) zoomBy(d / pinch, (a[0] + b[0]) / 2, (a[1] + b[1]) / 2);
      pinch = d;
      return;
    }
    cam.x -= (e.offsetX - px) / cam.scale;
    cam.z -= (e.offsetY - py) / cam.scale;
    pointers.set(e.pointerId, [e.offsetX, e.offsetY]);
    draw();
  });
  const up = (e) => { pointers.delete(e.pointerId); pinch = null; };
  view.addEventListener("pointerup", up);
  view.addEventListener("pointerleave", () => { coords.textContent = ""; });
  view.addEventListener("pointercancel", up);
  view.addEventListener("wheel", (e) => { e.preventDefault(); zoomBy(e.deltaY < 0 ? 1.2 : 1 / 1.2, e.offsetX, e.offsetY); }, { passive: false });
  view.addEventListener("dblclick", (e) => zoomBy(2, e.offsetX, e.offsetY));
  view.addEventListener("keydown", (e) => {
    const step = 80 / cam.scale;
    if (e.key === "+" || e.key === "=") zoomBy(1.4);
    else if (e.key === "-") zoomBy(1 / 1.4);
    else if (e.key === "ArrowLeft") { cam.x -= step; draw(); }
    else if (e.key === "ArrowRight") { cam.x += step; draw(); }
    else if (e.key === "ArrowUp") { cam.z -= step; draw(); }
    else if (e.key === "ArrowDown") { cam.z += step; draw(); }
    else return;
    e.preventDefault();
  });

  async function load() {
    try {
      const d = await api(`map/chunks?world=${encodeURIComponent(worldSel.value)}`);
      d.world = d.world || worldSel.value || "";
      data = d;
      computeAreas();
      document.getElementById("map-src").textContent = d.source === "bridge" ? `${num(d.chunks.length)} chunks loaded` : "Estimated (no plugin)";
      if (!fitted) { fitted = true; fit(); } else draw();
      renderSide();
      const ws = (S.server.bridge && S.server.bridge.worlds) || [];
      if (ws.length > 1 && worldSel.options.length !== ws.length) {
        clear(worldSel, ws.map((w) => h("option", { value: w.name, selected: w.name === d.world }, w.name)));
      }
    } catch (e) {
      clear(side, h("div", { class: "note warn" }, icon("alert"), h("div", null, e.message)));
    }
  }
  worldSel.addEventListener("change", () => { fitted = false; tiles.clear(); load(); });
  load();
  timer = setInterval(() => { if (alive) load(); }, 3000);
  setTimeout(size, 30);
  if (!S.server.bridge || !S.server.bridge.connected) toast("Map is estimated", "Install the OrbisFox Bridge plugin for the real loaded-chunk map.", "info");
}
