// The explored-world map, shown in the panel. The viewer itself (web/livemap) is a standalone page
// that does all its drawing in the browser; the same page is used for the network share and OBS.
import { h, icon, api, modal, clear, toast } from "./util.js";

export async function openMap() {
  const frameEl = h("iframe", { src: "/map/index.html?embed=1", title: "World map",
    style: { border: 0, width: "100%", height: "100%", display: "block", background: "#0A1416" } });
  const info = h("span", { class: "muted" }, "");
  const shareBox = h("div", { class: "row", style: { gap: "8px" } });
  const head = h("div", { class: "row wrap", style: { padding: "8px 16px", borderBottom: "1px solid var(--line)", gap: "12px" } },
    info, h("div", { class: "grow" }), shareBox,
    h("button", { class: "btn sm", onclick: () => window.open("/map/index.html", "_blank", "noopener") }, icon("external", "sm"), "Open in browser"));
  const body = h("div", { style: { display: "flex", flexDirection: "column", flex: 1, minHeight: 0 } },
    head, h("div", { style: { flex: 1, minHeight: 0 } }, frameEl));
  modal({ title: "World map", wide: true, body });
  try {
    const m = await api("map/info");
    info.textContent = m.regions
      ? `${m.regions.toLocaleString()} map regions explored  ·  ${m.bridge ? "growing as players explore" : "plugin not connected, so not growing right now"}`
      : m.bridge ? "Nothing captured yet. It fills in as players explore." : "Needs the OrbisFox Bridge plugin (Mods and logs → Install plugin).";
    clear(shareBox, m.public && m.urls.length
      ? [h("span", { class: "tag cyan" }, "Shared"), h("button", { class: "btn sm", title: m.urls[0], onclick: () => { navigator.clipboard.writeText(m.urls[0]); toast("Map link copied", `${m.urls[0]}  ·  Add &overlay=1 for OBS.`); } }, icon("copy", "sm"), "Copy share link")]
      : [h("button", { class: "btn sm", onclick: () => { document.querySelector(".modal-back")?.remove(); window.orbis.openPanel("settings"); setTimeout(() => document.getElementById("set-map")?.scrollIntoView({ behavior: "smooth" }), 300); } }, icon("wifi", "sm"), "Share on my network")]);
  } catch { /* the viewer still works */ }
}
