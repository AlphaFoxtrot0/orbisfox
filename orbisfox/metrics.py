"""CPU, memory and GPU usage of the server process and of the whole PC.

Windows uses kernel32/psapi and the PDH performance counters through ctypes (no extra packages);
Linux reads /proc and, when present, nvidia-smi. Everything degrades to None when unavailable.
"""
import os
import shutil
import subprocess
import sys
import time

IS_WIN = os.name == "nt"
CPUS = os.cpu_count() or 1

if IS_WIN:
    import ctypes
    from ctypes import wintypes as wt

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

    class _FILETIME(ctypes.Structure):
        _fields_ = [("lo", wt.DWORD), ("hi", wt.DWORD)]

    class _PMC_EX(ctypes.Structure):
        _fields_ = [("cb", wt.DWORD), ("PageFaultCount", wt.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
                    ("PrivateUsage", ctypes.c_size_t)]

    class _MEMSTATUS(ctypes.Structure):
        _fields_ = [("dwLength", wt.DWORD), ("dwMemoryLoad", wt.DWORD),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

    _k32.OpenProcess.restype = wt.HANDLE
    _k32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
    _k32.CloseHandle.argtypes = [wt.HANDLE]
    _k32.GetProcessTimes.argtypes = [wt.HANDLE] + [ctypes.POINTER(_FILETIME)] * 4
    _k32.GetSystemTimes.argtypes = [ctypes.POINTER(_FILETIME)] * 3
    _k32.K32GetProcessMemoryInfo.argtypes = [wt.HANDLE, ctypes.POINTER(_PMC_EX), wt.DWORD]
    _k32.GlobalMemoryStatusEx.argtypes = [ctypes.POINTER(_MEMSTATUS)]
    _k32.GetExitCodeProcess.argtypes = [wt.HANDLE, ctypes.POINTER(wt.DWORD)]

    def _ft(f):
        return ((f.hi << 32) | f.lo) / 1e7

    # --- PDH (GPU engine utilisation and GPU memory per process) -------------------------------
    class _PDH_VALUE(ctypes.Structure):
        _fields_ = [("CStatus", wt.DWORD), ("doubleValue", ctypes.c_double)]

    class _PDH_ITEM(ctypes.Structure):
        _fields_ = [("szName", wt.LPWSTR), ("FmtValue", _PDH_VALUE)]

    _PDH_FMT_DOUBLE = 0x00000200
    _PDH_FMT_NOCAP100 = 0x00008000
    _PDH_MORE_DATA = 0x800007D2

    class _PdhCounter:
        """One wildcard counter, read as {instance name: value}."""

        def __init__(self, path):
            self.ok = False
            try:
                self.pdh = ctypes.WinDLL("pdh")
                self.query = wt.HANDLE()
                self.counter = wt.HANDLE()
                if self.pdh.PdhOpenQueryW(None, None, ctypes.byref(self.query)) != 0:
                    return
                if self.pdh.PdhAddEnglishCounterW(self.query, path, None, ctypes.byref(self.counter)) != 0:
                    return
                self.pdh.PdhCollectQueryData(self.query)
                self.ok = True
            except OSError:
                self.ok = False

        def read(self):
            if not self.ok:
                return {}
            if self.pdh.PdhCollectQueryData(self.query) != 0:
                return {}
            size = wt.DWORD(0)
            count = wt.DWORD(0)
            fmt = _PDH_FMT_DOUBLE | _PDH_FMT_NOCAP100
            rc = self.pdh.PdhGetFormattedCounterArrayW(self.counter, fmt, ctypes.byref(size),
                                                       ctypes.byref(count), None)
            if rc & 0xFFFFFFFF != _PDH_MORE_DATA or size.value == 0:
                return {}
            buf = (ctypes.c_byte * size.value)()
            rc = self.pdh.PdhGetFormattedCounterArrayW(self.counter, fmt, ctypes.byref(size),
                                                       ctypes.byref(count), buf)
            if rc != 0:
                return {}
            items = ctypes.cast(buf, ctypes.POINTER(_PDH_ITEM))
            out = {}
            for i in range(count.value):
                it = items[i]
                if it.FmtValue.CStatus in (0, 1):  # PDH_CSTATUS_VALID_DATA / NEW_DATA
                    out[it.szName] = out.get(it.szName, 0.0) + it.FmtValue.doubleValue
            return out


class Sampler:
    """Call sample(pid) every couple of seconds; it returns a dict of numbers (or None)."""

    def __init__(self):
        self._last_proc = None   # (pid, wall, cpu_seconds)
        self._last_sys = None    # (idle, total)
        self._gpu_util = None
        self._gpu_mem = None
        self._gpu_tried = False
        self._nvsmi = shutil.which("nvidia-smi") if not IS_WIN else None
        self._nv_last = (0.0, None)

    # -- process ---------------------------------------------------------------------------
    def _proc_win(self, pid):
        h = _k32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not h:
            return None
        try:
            c, e, k, u = _FILETIME(), _FILETIME(), _FILETIME(), _FILETIME()
            if not _k32.GetProcessTimes(h, ctypes.byref(c), ctypes.byref(e), ctypes.byref(k), ctypes.byref(u)):
                return None
            cpu = _ft(k) + _ft(u)
            pmc = _PMC_EX()
            pmc.cb = ctypes.sizeof(pmc)
            rss = priv = None
            if _k32.K32GetProcessMemoryInfo(h, ctypes.byref(pmc), pmc.cb):
                rss, priv = pmc.WorkingSetSize, pmc.PrivateUsage
            return cpu, rss, priv
        finally:
            _k32.CloseHandle(h)

    def _proc_linux(self, pid):
        try:
            with open(f"/proc/{pid}/stat") as f:
                parts = f.read().rsplit(")", 1)[1].split()
            tick = os.sysconf("SC_CLK_TCK")
            cpu = (int(parts[11]) + int(parts[12])) / tick
            rss = int(parts[21]) * os.sysconf("SC_PAGE_SIZE")
            return cpu, rss, rss
        except (OSError, ValueError, IndexError):
            return None

    # -- whole system ----------------------------------------------------------------------
    def _sys_cpu(self):
        if IS_WIN:
            i, k, u = _FILETIME(), _FILETIME(), _FILETIME()
            if not _k32.GetSystemTimes(ctypes.byref(i), ctypes.byref(k), ctypes.byref(u)):
                return None
            idle, total = _ft(i), _ft(k) + _ft(u)   # kernel time includes idle time
        else:
            try:
                with open("/proc/stat") as f:
                    v = [int(x) for x in f.readline().split()[1:]]
                idle, total = v[3] + (v[4] if len(v) > 4 else 0), sum(v)
            except (OSError, ValueError):
                return None
        last, self._last_sys = self._last_sys, (idle, total)
        if not last or total <= last[1]:
            return None
        return max(0.0, min(100.0, 100.0 * (1 - (idle - last[0]) / (total - last[1]))))

    @staticmethod
    def system_memory():
        if IS_WIN:
            m = _MEMSTATUS()
            m.dwLength = ctypes.sizeof(m)
            if _k32.GlobalMemoryStatusEx(ctypes.byref(m)):
                return m.ullTotalPhys, m.ullTotalPhys - m.ullAvailPhys
            return None, None
        try:
            info = {}
            with open("/proc/meminfo") as f:
                for line in f:
                    k, v = line.split(":", 1)
                    info[k] = int(v.split()[0]) * 1024
            return info["MemTotal"], info["MemTotal"] - info.get("MemAvailable", info["MemFree"])
        except (OSError, KeyError, ValueError):
            return None, None

    # -- GPU -------------------------------------------------------------------------------
    def _gpu_win(self, pid):
        if not self._gpu_tried:
            self._gpu_tried = True
            self._gpu_util = _PdhCounter(r"\GPU Engine(*)\Utilization Percentage")
            self._gpu_mem = _PdhCounter(r"\GPU Process Memory(*)\Dedicated Usage")
        if not self._gpu_util or not self._gpu_util.ok:
            return None, None, None
        util = self._gpu_util.read()
        # Task Manager's method: per engine type, sum the instances; the busiest type wins.
        per_engine, per_type_proc = {}, {}
        tag = f"pid_{pid}_" if pid else None
        for name, val in util.items():
            parts = name.split("_")
            try:
                eng = "_".join(parts[parts.index("luid"):parts.index("engtype")])
                etype = parts[parts.index("engtype") + 1]
            except (ValueError, IndexError):
                continue
            per_engine[eng] = per_engine.get(eng, 0.0) + val
            if tag and name.startswith(tag):
                per_type_proc[etype] = per_type_proc.get(etype, 0.0) + val
        sys_gpu = min(100.0, max(per_engine.values())) if per_engine else 0.0
        proc_gpu = min(100.0, max(per_type_proc.values())) if per_type_proc else (0.0 if pid else None)
        vram = None
        if pid and self._gpu_mem and self._gpu_mem.ok:
            vram = sum(v for n, v in self._gpu_mem.read().items() if n.startswith(tag)) or 0
        return proc_gpu, sys_gpu, vram

    def _gpu_nvidia(self, pid):
        now = time.time()
        if now - self._nv_last[0] < 4:
            return self._nv_last[1] or (None, None, None)
        res = (None, None, None)
        try:
            out = subprocess.run([self._nvsmi, "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"],
                                 capture_output=True, text=True, timeout=3).stdout
            sys_gpu = max(float(x) for x in out.split() if x.strip())
            vram = None
            if pid:
                out = subprocess.run([self._nvsmi, "--query-compute-apps=pid,used_memory",
                                      "--format=csv,noheader,nounits"],
                                     capture_output=True, text=True, timeout=3).stdout
                for line in out.splitlines():
                    p, _, mem = line.partition(",")
                    if p.strip() == str(pid):
                        vram = float(mem) * 1024 * 1024
            res = (None, sys_gpu, vram)
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
        self._nv_last = (now, res)
        return res

    def sample(self, pid, gpu=False):
        now = time.time()
        out = {"t": now, "cpu": None, "rss": None, "private": None, "sys_cpu": self._sys_cpu(),
               "gpu": None, "sys_gpu": None, "vram": None}
        total, used = self.system_memory()
        out["sys_mem_total"], out["sys_mem_used"] = total, used
        if pid:
            p = self._proc_win(pid) if IS_WIN else self._proc_linux(pid)
            if p:
                cpu_s, out["rss"], out["private"] = p
                last = self._last_proc
                if last and last[0] == pid and now > last[1]:
                    # Percent of the whole machine, like Task Manager (100% = every core busy).
                    out["cpu"] = max(0.0, min(100.0, 100.0 * (cpu_s - last[2]) / (now - last[1]) / CPUS))
                self._last_proc = (pid, now, cpu_s)
        try:
            if not gpu:
                pass
            elif IS_WIN:
                out["gpu"], out["sys_gpu"], out["vram"] = self._gpu_win(pid)
            elif self._nvsmi:
                out["gpu"], out["sys_gpu"], out["vram"] = self._gpu_nvidia(pid)
        except Exception:  # GPU counters are a nicety; never let them break sampling
            pass
        return out


def disk_free(path):
    try:
        u = shutil.disk_usage(path)
        return {"total": u.total, "free": u.free}
    except OSError:
        return None


def folder_size(path, budget_s=3.0):
    """Size of a folder in bytes, giving up (returning what it has) after budget_s seconds."""
    total, start = 0, time.time()
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
        if time.time() - start > budget_s:
            break
    return total


if __name__ == "__main__":  # quick manual check: python -m orbisfox.metrics <pid>
    s = Sampler()
    pid = int(sys.argv[1]) if len(sys.argv) > 1 else os.getpid()
    for _ in range(3):
        time.sleep(1)
        print(s.sample(pid))
