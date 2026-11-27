"""Windows Firewall: letting other devices reach the shared map (and the panel, when allowed).

A Python program that listens on the network triggers Windows' "Allow access?" prompt the first
time. That prompt often opens behind other windows, and dismissing it saves *block* rules for
Python, which beat any allow rule. The result is a connection that just times out from other
devices. OrbisFox checks for its own allow rule and for inbound rules that block Python, and can fix
both in one step after Windows asks for administrator permission on the server PC.
"""
import base64
import json
import os
import subprocess
import sys
import threading
import time

NO_WINDOW = 0x08000000 if os.name == "nt" else 0
RULE = "OrbisFox"
_cache = {"t": 0, "key": None, "value": None}
_lock = threading.Lock()


def _programs():
    exe = os.path.abspath(sys.executable)
    folder = os.path.dirname(exe)
    out = {exe}
    for name in ("python.exe", "pythonw.exe"):
        p = os.path.join(folder, name)
        if os.path.isfile(p):
            out.add(p)
    return sorted(out)


def _ps(script, timeout=30):
    enc = base64.b64encode(script.encode("utf-16-le")).decode()
    return subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", enc],
                          capture_output=True, text=True, timeout=timeout, creationflags=NO_WINDOW)


def _quote(s):
    return "'" + str(s).replace("'", "''") + "'"


def status(ports, max_age=60):
    """{"supported", "allowed": [ports our rule opens], "missing": [ports], "blocking": [rule names],
    "ok": bool}. Cached for a minute: asking Windows takes about a second."""
    ports = sorted({int(p) for p in ports if p})
    if os.name != "nt":
        return {"supported": False, "ok": True, "ports": ports, "allowed": [], "missing": [], "blocking": []}
    key = tuple(ports)
    with _lock:
        if _cache["key"] == key and time.time() - _cache["t"] < max_age and _cache["value"]:
            return _cache["value"]
    progs = ",".join(_quote(p) for p in _programs())
    script = f"""
$ErrorActionPreference = 'SilentlyContinue'
$progs = @({progs})
$ours = Get-NetFirewallRule -DisplayName {_quote(RULE)} | Where-Object {{ $_.Enabled -eq 'True' -and $_.Direction -eq 'Inbound' -and $_.Action -eq 'Allow' }}
$open = @()
foreach ($r in $ours) {{ $open += @(($r | Get-NetFirewallPortFilter).LocalPort) }}
$block = Get-NetFirewallApplicationFilter | Where-Object {{ $progs -contains $_.Program }} | Get-NetFirewallRule |
  Where-Object {{ $_.Enabled -eq 'True' -and $_.Direction -eq 'Inbound' -and $_.Action -eq 'Block' }}
@{{ open = @($open | ForEach-Object {{ "$_" }}); blocking = @($block | ForEach-Object {{ $_.DisplayName }}) }} | ConvertTo-Json -Compress
"""
    try:
        r = _ps(script)
        data = json.loads((r.stdout or "").strip() or "{}")
    except (OSError, subprocess.SubprocessError, ValueError):
        return {"supported": True, "ok": None, "ports": ports, "allowed": [], "missing": ports, "blocking": [],
                "error": "Couldn't ask Windows Firewall."}
    opened = set()
    for item in data.get("open") or []:
        for part in str(item).split(","):
            part = part.strip()
            if part.isdigit():
                opened.add(int(part))
            elif "-" in part:
                a, _, b = part.partition("-")
                if a.isdigit() and b.isdigit():
                    opened.update(p for p in ports if int(a) <= p <= int(b))
            elif part.lower() == "any":
                opened.update(ports)
    blocking = sorted(set(data.get("blocking") or []))
    missing = [p for p in ports if p not in opened]
    value = {"supported": True, "ok": not missing and not blocking, "ports": ports,
             "allowed": sorted(opened & set(ports)), "missing": missing, "blocking": blocking}
    with _lock:
        _cache.update(t=time.time(), key=key, value=value)
    return value


def allow(ports):
    """Adds (or updates) OrbisFox's inbound rule for these TCP ports (devices on the local network
    only, on every network type), and
    turns off inbound rules that block this Python. Windows shows its administrator prompt on the
    server PC. Returns status() afterwards; raises RuntimeError when it didn't happen."""
    if os.name != "nt":
        raise RuntimeError("Only needed on Windows.")
    ports = sorted({int(p) for p in ports if p})
    if not ports:
        raise RuntimeError("Nothing to open: turn on map sharing or network access first.")
    progs = ",".join(_quote(p) for p in _programs())
    plist = ",".join(_quote(p) for p in ports)
    inner = f"""
$ErrorActionPreference = 'Stop'
Get-NetFirewallRule -DisplayName {_quote(RULE)} -ErrorAction SilentlyContinue | Remove-NetFirewallRule
New-NetFirewallRule -DisplayName {_quote(RULE)} -Direction Inbound -Action Allow -Protocol TCP -LocalPort @({plist}) -RemoteAddress LocalSubnet -Profile Any -Description 'OrbisFox: the shared world map and panel, read by devices on your network.' | Out-Null
$progs = @({progs})
Get-NetFirewallApplicationFilter | Where-Object {{ $progs -contains $_.Program }} | Get-NetFirewallRule |
  Where-Object {{ $_.Direction -eq 'Inbound' -and $_.Action -eq 'Block' }} | Disable-NetFirewallRule
"""
    enc = base64.b64encode(inner.encode("utf-16-le")).decode()
    outer = (f"$p = Start-Process powershell -Verb RunAs -WindowStyle Hidden -Wait -PassThru "
             f"-ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-EncodedCommand','{enc}'; exit $p.ExitCode")
    try:
        r = _ps(outer, timeout=180)
    except subprocess.TimeoutExpired:
        raise RuntimeError("Windows didn't answer. Look for its permission prompt on the server PC.")
    if r.returncode != 0:
        msg = (r.stderr or r.stdout or "").strip()
        if "canceled" in msg.lower() or "cancelled" in msg.lower():
            raise RuntimeError("Windows' permission prompt was declined, so nothing changed.")
        raise RuntimeError("Windows Firewall wasn't changed" + (f": {msg.splitlines()[-1][:200]}" if msg else "."))
    with _lock:
        _cache["value"] = None
    return status(ports)
