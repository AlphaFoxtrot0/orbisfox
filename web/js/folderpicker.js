// Folder picker shown inside the OrbisFox window (a native dialog opened by the background process
// tends to appear behind the app). Highlights folders that hold a Hytale server.
import { h, icon, api, modal, clear } from "./util.js";

export function pickServerFolder(start = "") {
  return new Promise((resolve) => {
    let done = false, current = null;
    const crumb = h("div", { class: "mono", style: { fontSize: "13px", color: "var(--ink-muted)", wordBreak: "break-all", minHeight: "20px" } });
    const status = h("div", { class: "muted", style: { minHeight: "22px", fontSize: "15px" } });
    const list = h("div", { class: "list", style: { maxHeight: "46vh", overflowY: "auto", border: "1px solid var(--line)", borderRadius: "8px", padding: "4px" } });
    const placesRow = h("div", { class: "row wrap", style: { gap: "6px" } });
    const useBtn = h("button", { class: "btn primary", disabled: true, onclick: () => finish(current && current.server && current.server.server_dir) }, icon("check"), "Use this folder");
    const upBtn = h("button", { class: "btn sm", disabled: true, onclick: () => current && current.parent && go(current.parent) }, icon("upload", "sm"), "Up");
    const findBtn = h("button", { class: "btn sm", onclick: find }, icon("search", "sm"), "Find servers on this PC");

    function finish(path) { done = true; resolve(path || null); m.close(); }

    async function go(path) {
      status.textContent = "Loading...";
      let d;
      try { d = await api(`fs/list?path=${encodeURIComponent(path || "")}`); }
      catch (e) { status.textContent = e.message; return; }
      current = d;
      crumb.textContent = d.path || "This PC";
      upBtn.disabled = !d.parent && !d.path;
      upBtn.onclick = () => go(d.parent || "");
      useBtn.disabled = !d.server;
      status.replaceChildren(d.server
        ? h("span", { style: { color: "var(--cyan)" } }, `Hytale server found${d.server.version ? ` (version ${d.server.version})` : ""}${d.server.world ? ", with a world" : ""}. Press Use this folder.`)
        : d.path ? "Open the folder that holds HytaleServer.jar (the Server folder of the download)." : "Pick a starting place.");
      clear(placesRow, d.places.map((p) => h("button", { class: "chip", onclick: () => go(p.path) }, icon(p.name.length <= 3 ? "folder" : "folder"), p.name)));
      clear(list, d.folders.length ? d.folders.map((f) => h("div", { class: "item clickable", style: { minHeight: "40px" }, ondblclick: () => (f.server ? finish(f.path) : go(f.path)), onclick: () => go(f.path) },
        icon(f.server ? "cube" : "folder"), h("div", { class: "main" }, h("div", { class: "name", style: { fontWeight: f.server ? 700 : 500 } }, f.name)),
        f.server ? h("span", { class: "tag cyan" }, f.version ? `Hytale server ${f.version}` : "Hytale server") : null))
        : h("div", { class: "empty" }, d.path ? "No folders in here." : ""));
    }

    async function find() {
      findBtn.disabled = true;
      status.textContent = "Looking in your home folder and drives (a few seconds)...";
      let r;
      try { r = await api("fs/find"); } catch (e) { status.textContent = e.message; findBtn.disabled = false; return; }
      findBtn.disabled = false;
      if (!r.servers.length) {
        status.textContent = r.complete ? "No Hytale server found in the usual places. Browse to it instead." : "Nothing found yet in the time allowed. Browse to it instead.";
        return;
      }
      status.textContent = `Found ${r.servers.length} server${r.servers.length > 1 ? "s" : ""}. Pick one:`;
      clear(list, r.servers.map((s) => h("div", { class: "item clickable", style: { minHeight: "48px" }, onclick: () => finish(s.path) },
        icon("cube"), h("div", { class: "main" }, h("div", { class: "name mono", style: { fontSize: "13px", whiteSpace: "normal", wordBreak: "break-all" } }, s.path),
          h("div", { class: "sub" }, [s.version ? `Version ${s.version}` : null, s.world ? "has a world" : "no world yet"].filter(Boolean).join("  ·  "))),
        h("span", { class: "tag cyan" }, "Use"))));
    }

    const m = modal({
      title: "Pick your server folder",
      body: h("div", { class: "col", style: { gap: "10px" } }, placesRow, h("div", { class: "row" }, upBtn, h("div", { class: "grow" }, crumb), findBtn), list, status),
      actions: [{ label: "Cancel" }],
      onClose: () => { if (!done) resolve(null); },
    });
    m.el.querySelector(".modal-foot").append(useBtn);
    m.el.style.width = "min(720px, 100%)";
    go(start);
  });
}
