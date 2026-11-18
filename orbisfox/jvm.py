"""Reads the Java garbage-collector log that OrbisFox asks the server to write, and turns it into
"how full is the heap really" and "how much time is the server losing to GC".

When players spread out and load chunks in many places at once, every loaded chunk stays in the
Java heap. Once the heap is nearly full the collector runs back to back, each pause freezing the
world, and the server looks hung even though the process is alive. This module measures exactly
that, so the watchdog can act before the players give up.
"""
import collections
import os
import re
import shutil
import subprocess
import sys
import threading
import time

_UNIT = {"K": 1 / 1024, "M": 1, "G": 1024}
# G1 / Parallel / Serial:  GC(12) Pause Young (Normal) (G1 Evacuation Pause) 2048M->1024M(4096M) 15.123ms
_N = r"(\d+(?:\.\d+)?)"
_PAUSE = re.compile(r"GC\((\d+)\)\s+(Pause .*?)\s+" + _N + r"([KMG])->" + _N + r"([KMG])\(" + _N + r"([KMG])\)\s+([\d.]+)ms")
# ZGC:  GC(3) Major Collection (Allocation Rate) 1234M(30%)->456M(11%) 0.523s
_ZGC = re.compile(r"GC\((\d+)\)\s+(.*?Collection.*?)\s+(\d+)M\(\d+%\)->(\d+)M\((\d+)%\)\s+([\d.]+)(ms|s)\b")
_UPTIME = re.compile(r"\[(\d+(?:\.\d+)?)s\]")


def gc_log_args(path):
    """JVM flags that write a compact GC log to `path` (one line per collection)."""
    return [f"-Xlog:gc:file={path}:time,uptime,level,tags:filecount=3,filesize=20M"]


class GcMonitor:
    """Tails one GC log file in a background thread and keeps a short history of collections."""

    def __init__(self, on_event=None):
        self.on_event = on_event
        self._lock = threading.Lock()
        self.events = collections.deque(maxlen=4000)   # (wall_time, kind, before_mb, after_mb, cap_mb, pause_ms)
        self.path = None
        self.max_heap_mb = None
        self._stop = threading.Event()
        self._thread = None

    def start(self, path, max_heap_mb):
        self.stop()
        with self._lock:
            self.events.clear()
        self.path, self.max_heap_mb = path, max_heap_mb
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="gc-tail", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._thread = None

    def _run(self):
        pos, buf, stop = 0, "", self._stop
        while not stop.is_set():
            try:
                size = os.path.getsize(self.path)
                if size < pos:          # rotated
                    pos = 0
                if size > pos:
                    with open(self.path, "r", encoding="utf-8", errors="replace") as f:
                        f.seek(pos)
                        buf += f.read()
                        pos = f.tell()
                    *lines, buf = buf.split("\n")
                    for line in lines:
                        self.feed(line)
            except OSError:
                pass
            stop.wait(1.0)

    def feed(self, line, now=None):
        now = now or time.time()
        m = _PAUSE.search(line)
        if m:
            kind = m.group(2).strip()
            before = float(m.group(3)) * _UNIT[m.group(4)]
            after = float(m.group(5)) * _UNIT[m.group(6)]
            cap = float(m.group(7)) * _UNIT[m.group(8)]
            ev = (now, kind, before, after, cap, float(m.group(9)))
        else:
            m = _ZGC.search(line)
            if not m:
                return
            pause = float(m.group(6)) * (1000 if m.group(7) == "s" else 1)
            # ZGC collections are concurrent; count only a small share of their length as "paused".
            ev = (now, m.group(2).strip(), float(m.group(3)), float(m.group(4)),
                  self.max_heap_mb or 0, pause * 0.02)
        with self._lock:
            self.events.append(ev)
        if self.on_event:
            self.on_event(ev)

    def summary(self, window_s=120, now=None):
        """Heap and GC figures over the last `window_s` seconds."""
        now = now or time.time()
        with self._lock:
            recent = [e for e in self.events if now - e[0] <= window_s]
            last = self.events[-1] if self.events else None
        cap = self.max_heap_mb or (last[4] if last else None)
        out = {"max_mb": cap, "used_after_gc_mb": None, "live_percent": None, "gc_overhead_percent": 0.0,
               "full_gcs": 0, "collections": len(recent), "longest_pause_ms": 0.0, "last_gc_age_s": None}
        if last:
            out["last_gc_age_s"] = round(now - last[0], 1)
            out["used_after_gc_mb"] = round(last[3])
        if recent:
            paused = sum(e[5] for e in recent)
            span = max(window_s, 1) * 1000
            out["gc_overhead_percent"] = round(min(100.0, 100.0 * paused / span), 1)
            out["full_gcs"] = sum(1 for e in recent if "Full" in e[1])
            out["longest_pause_ms"] = round(max(e[5] for e in recent), 1)
            # The lowest the heap got after the last few collections = memory that is truly live.
            live = min(e[3] for e in recent[-5:])
            if cap:
                out["live_percent"] = round(100.0 * live / cap, 1)
        elif last and cap:
            out["live_percent"] = round(100.0 * last[3] / cap, 1)
        return out


def java_cmd(java):
    """The argv prefix that runs `java`. A .py path is the demo server, run with this Python."""
    if java and java.lower().endswith(".py"):
        return [sys.executable, java]
    return [java]


def find_java(preferred=""):
    """Returns (path, major_version) of the best Java to use, or (None, None)."""
    if preferred and preferred.lower().endswith(".py") and os.path.isfile(preferred):
        return preferred, java_version(preferred)
    candidates = []
    if preferred:
        candidates.append(preferred)
    jh = os.environ.get("JAVA_HOME")
    if jh:
        candidates.append(os.path.join(jh, "bin", "java.exe" if os.name == "nt" else "java"))
    if os.name == "nt":
        for base in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                     os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")):
            for vendor in ("Java", "Eclipse Adoptium", "Microsoft", "Zulu", "Amazon Corretto", "BellSoft"):
                root = os.path.join(base, vendor)
                if os.path.isdir(root):
                    for d in sorted(os.listdir(root), reverse=True):
                        candidates.append(os.path.join(root, d, "bin", "java.exe"))
    found = shutil.which("java")
    if found:
        candidates.append(found)
    best = (None, None)
    seen = set()
    for c in candidates:
        if not c or c in seen or not (os.path.isfile(c) or shutil.which(c)):
            continue
        seen.add(c)
        v = java_version(c)
        if v and (best[1] is None or v > best[1]):
            best = (c, v)
        if v and v >= 25 and c == preferred:
            return c, v
    return best


def java_version(java):
    try:
        out = subprocess.run(java_cmd(java) + ["-version"], capture_output=True, text=True, timeout=10,
                             creationflags=0x08000000 if os.name == "nt" else 0)
        m = re.search(r'version "(\d+)(?:\.(\d+))?', out.stderr + out.stdout)
        if not m:
            return None
        major = int(m.group(1))
        return int(m.group(2)) if major == 1 and m.group(2) else major
    except (OSError, subprocess.SubprocessError):
        return None
