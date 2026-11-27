// Windows Firewall check for the map share and network access (see orbisfox/firewall.py).
// A dismissed "Allow access?" prompt leaves Python blocked, and other devices then just time out.
import { h, icon, api, act, clear, confirmBox, toast } from "./util.js";

export function firewallNote(style = {}) {
  const box = h("div", { style });
  load(box, false);
  return box;
}

async function load(box, fresh) {
  let st;
  try { st = await api(`firewall${fresh ? "?fresh=1" : ""}`); } catch { return; }
  if (!st.supported || st.ok !== false || !st.ports.length) { clear(box); return; }
  const plural = st.missing.length > 1 ? "s" : "";
  const why = st.blocking.length
    ? `Windows has rules blocking Python (${st.blocking.join(", ")}), probably from an "Allow access?" prompt that was dismissed.`
    : `Windows Firewall isn't letting other devices reach port${plural} ${st.missing.join(", ")}.`;
  clear(box, h("div", { class: "note warn", style: { marginBottom: "12px" } }, icon("shield"),
    h("div", { class: "grow" }, h("b", null, "Other devices can't connect yet. "), why,
      " On the other device that looks like a connection timeout."),
    h("button", { class: "btn sm primary", style: { flex: "none" }, onclick: () => fix(box, st) }, "Allow through firewall")));
}

async function fix(box, st) {
  const ports = st.ports.join(" and ");
  const extra = st.blocking.length ? " It also turns off the rules that block Python." : "";
  const ok = await confirmBox("Allow OrbisFox through Windows Firewall?",
    `Windows asks for administrator permission on the server PC (look for its prompt there). OrbisFox then adds one rule that lets devices on your local network reach port ${ports}.${extra} Devices outside your network still can't connect.`,
    "Allow");
  if (!ok) return;
  toast("Waiting for Windows", "Approve the prompt on the server PC.", "info", 8000);
  const r = await act("firewall/allow", {});
  if (r && r.ok) toast("Other devices can connect now", `Port ${ports} is open to your local network.`);
  load(box, true);
}
