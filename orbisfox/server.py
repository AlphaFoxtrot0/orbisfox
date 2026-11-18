"""Runs the Hytale server as a child process: start, stop, restart, console in and out.

States: stopped, starting, running, stopping, crashed. A timed stop/restart that is counting down
is reported separately as `pending`, so the console stays usable while players are warned.
"""
import os
import shlex
import shutil
import subprocess
import threading
import time

from . import hytale, jvm

NO_WINDOW = 0x08000000 if os.name == "nt" else 0
NEW_GROUP = 0x00000200 if os.name == "nt" else 0
UPDATE_EXIT_CODE = 8
GC_LOG_REL = os.path.join("logs", "orbisfox-gc.log")


class Supervisor:
    def __init__(self, core):
        self.core = core
        self.proc = None
        self.state = "stopped"
        self.started_at = None
        self.ready_at = None
        self.exit_code = None
        self.pending = None          # {"action", "at", "reason"} for a countdown in progress
        self._pending_cancel = None
        self._want_restart = False
        self._lock = threading.RLock()
        self._crashes = []           # times of recent crashes, for crash-loop protection
        self.last_error = None
        self.launch_info = None

    # ----------------------------------------------------------------------------- state
    @property
    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    @property
    def running(self):
        return self.alive and self.state == "running"

    @property
    def pid(self):
        return self.proc.pid if self.alive else None

    def info(self):
        return {"state": self.state, "pid": self.pid, "started_at": self.started_at, "ready_at": self.ready_at,
                "exit_code": self.exit_code, "pending": self.pending, "last_error": self.last_error,
                "launch": self.launch_info}

    def _set(self, state):
        self.state = state
        self.core.publish_server()

    # ----------------------------------------------------------------------------- start
    def build_command(self):
        s = self.core.settings
        lay = hytale.locate(s["server_dir"])
        if lay["problems"]:
            raise RuntimeError(" ".join(lay["problems"]))
        java, major = jvm.find_java(s["java"])
        if not java:
            raise RuntimeError("Java was not found. Install Java 25 (for example Eclipse Temurin 25) "
                               "or set its path in Settings.")
        if major and major < 25:
            raise RuntimeError(f"Hytale needs Java 25, but the Java found is version {major} ({java}). "
                               "Install Java 25 or set its path in Settings.")
        sd = lay["server_dir"]
        args = jvm.java_cmd(java)
        if lay["jvm_options"]:
            args.append("@" + lay["jvm_options"])
        lo, hi = float(s["ram_min_gb"]), float(s["ram_max_gb"])
        lo = min(lo, hi)
        args += [f"-Xms{int(lo * 1024)}M", f"-Xmx{int(hi * 1024)}M"]
        user_jvm = shlex.split(s["jvm_args"], posix=os.name != "nt") if s["jvm_args"].strip() else []
        if not any(a.startswith(("-XX:+Use", "-XX:-Use")) and "GC" in a for a in user_jvm):
            args += ["-XX:+UseG1GC", "-XX:MaxGCPauseMillis=200"]
        if s["exit_on_oom"]:
            args.append("-XX:+ExitOnOutOfMemoryError")
        if s["aot_cache"] and lay["aot"]:
            args.append("-XX:AOTCache=HytaleServer.aot")
        if s["gc_log"]:
            os.makedirs(os.path.join(sd, "logs"), exist_ok=True)
            for n in os.listdir(os.path.join(sd, "logs")):
                if n.startswith("orbisfox-gc.log"):
                    try:
                        os.remove(os.path.join(sd, "logs", n))
                    except OSError:
                        pass
            args += jvm.gc_log_args(GC_LOG_REL.replace("\\", "/"))
        args += ["-Dstdout.encoding=UTF-8", "-Dstderr.encoding=UTF-8", "-Dfile.encoding=UTF-8"]
        args += user_jvm
        args += ["-jar", "HytaleServer.jar", "--assets", os.path.relpath(lay["assets"], sd)]
        if s["bind"].strip():
            args += ["--bind", s["bind"].strip()]
        b = s["backups"]
        if b.get("hytale_builtin", True):
            args += ["--backup", "--backup-dir", b.get("hytale_dir") or "backups",
                     "--backup-frequency", str(int(b.get("hytale_frequency") or 30))]
        if s["server_args"].strip():
            args += shlex.split(s["server_args"], posix=os.name != "nt")
        return args, lay, hi

    def start(self, reason="manual"):
        with self._lock:
            if self.alive:
                raise RuntimeError("The server is already running.")
            self.last_error = None
            try:
                args, lay, heap_gb = self.build_command()
            except RuntimeError as e:
                self.last_error = str(e)
                self.core.publish_server()
                raise
            sd = lay["server_dir"]
            env = dict(os.environ)
            env["ORBISFOX_BRIDGE_PORT"] = str(self.core.settings["plugin_port"])
            env["ORBISFOX_BRIDGE_TOKEN"] = self.core.bridge.token
            self.exit_code = None
            self.ready_at = None
            self.started_at = time.time()
            self.launch_info = {"command": subprocess.list2cmdline(args), "cwd": sd}
            self._set("starting")
            self.core.on_server_starting()
            self.core.log_app(f"Starting the server ({reason})")
            try:
                self.proc = subprocess.Popen(args, cwd=sd, env=env, stdin=subprocess.PIPE,
                                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                             bufsize=0, creationflags=NO_WINDOW | NEW_GROUP)
            except OSError as e:
                self.last_error = f"Could not start Java: {e}"
                self._set("stopped")
                raise RuntimeError(self.last_error)
            self.core.write_pidfile(self.proc.pid)
            if self.core.settings["gc_log"]:
                self.core.gc.start(os.path.join(sd, GC_LOG_REL), heap_gb * 1024)
            proc = self.proc
            threading.Thread(target=self._read, args=(proc,), name="server-stdout", daemon=True).start()
            threading.Thread(target=self._wait, args=(proc,), name="server-wait", daemon=True).start()

    def _read(self, proc):
        stream = proc.stdout
        while True:
            try:
                raw = stream.readline()
            except (OSError, ValueError):
                break
            if not raw:
                break
            try:
                self.core.on_line(raw.decode("utf-8", "replace").rstrip("\r\n"))
            except Exception as e:  # noqa: BLE001 - never stop reading the console
                self.core.log_app(f"Console handling error: {e}", level="error")

    def mark_ready(self):
        if self.state == "starting":
            self.ready_at = time.time()
            self._set("running")
            self.core.on_server_ready()

    def _wait(self, proc):
        code = proc.wait()
        time.sleep(0.3)   # let the reader drain the last lines
        with self._lock:
            if proc is not self.proc:
                return
            was = self.state
            self.exit_code = code
            self.proc = None
            self.core.gc.stop()
            self.core.clear_pidfile()
            self.core.on_server_exit(code)
            if code == UPDATE_EXIT_CODE:
                self.core.log_app("The server stopped to install an update (exit code 8).")
                self._set("stopped")
                threading.Thread(target=self._apply_update_and_start, daemon=True).start()
                return
            if was == "stopping" or self._want_restart:
                self._set("stopped")
                self.core.log_app(f"Server stopped (exit code {code}).")
                if self._want_restart:
                    self._want_restart = False
                    threading.Thread(target=self._delayed_start, args=(2, "restart"), daemon=True).start()
                return
            # Anything else is a crash (or a hang the watchdog killed).
            self._set("crashed")
            self.core.log_app(f"The server stopped unexpectedly (exit code {code}).", level="error")
            self.core.toast("Server crashed", f"Exit code {code}. Check the console for the last errors.", "bad")
            now = time.time()
            self._crashes = [t for t in self._crashes if now - t < 600] + [now]
            if self.core.settings["auto_restart"]:
                if len(self._crashes) >= 3:
                    self.core.log_app("Crashed 3 times in 10 minutes: not restarting automatically.", level="error")
                    self.core.toast("Auto-restart paused", "The server crashed 3 times in 10 minutes.", "bad", 0)
                else:
                    delay = 10 * len(self._crashes)
                    self.core.log_app(f"Restarting in {delay} seconds.")
                    threading.Thread(target=self._delayed_start, args=(delay, "after a crash"), daemon=True).start()

    def _delayed_start(self, delay, reason):
        time.sleep(delay)
        if not self.alive:
            try:
                self.start(reason)
            except RuntimeError as e:
                self.core.log_app(f"Could not start: {e}", level="error")

    def _apply_update_and_start(self):
        try:
            self.core.updates.apply_staged()
        except Exception as e:  # noqa: BLE001
            self.core.log_app(f"Applying the staged update failed: {e}", level="error")
            self.core.toast("Update failed", str(e), "bad", 0)
        self._delayed_start(1, "after an update")

    # ----------------------------------------------------------------------------- input
    def send(self, line, echo=True, source="user"):
        """Writes one console command. Returns the line as sent."""
        line = (line or "").strip()
        if not line:
            raise ValueError("Type a command first.")
        if "\n" in line or "\r" in line:
            raise ValueError("One command at a time.")
        if line.startswith("/"):
            line = line[1:]
        if not self.alive:
            raise RuntimeError("The server is not running.")
        if echo:
            self.core.echo_command(line, source)
        try:
            self.proc.stdin.write((hytale.stdin_escape(line) + "\n").encode("utf-8"))
            self.proc.stdin.flush()
        except (OSError, AttributeError) as e:
            raise RuntimeError(f"The server's console is not accepting input: {e}")
        return line

    def say(self, text, style="chat", level=""):
        text = hytale.command_text(text)
        if not text:
            raise ValueError("Write a message first.")
        if style == "title":
            return self.send(f"eventtitle {text}")
        if style == "notify":
            lvl = level if level in hytale.NOTIFY_STYLES else ""
            return self.send(f"notify {lvl + ' ' if lvl else ''}{text}")
        return self.send(f"say {text}")

    def flush_world(self, timeout=20):
        """Asks the server to save every world and waits (briefly) for it to finish."""
        if not self.running:
            return False
        mark = self.core.console_seq()
        try:
            self.send("world save --all --confirm", source="orbisfox")
        except RuntimeError:
            return False
        end = time.time() + timeout
        while time.time() < end:
            for ln in self.core.console_since(mark):
                t = ln["text"].lower()
                if "saved" in t or "saving complete" in t or "finished saving" in t:
                    return True
            time.sleep(0.5)
        return True   # no confirmation line is known for every version; assume it ran

    # ----------------------------------------------------------------------------- stop
    def stop(self, reason="manual", restart=False):
        with self._lock:
            self.cancel_pending(quiet=True)
            if not self.alive:
                if restart:
                    threading.Thread(target=self._delayed_start, args=(0, reason), daemon=True).start()
                return
            self._want_restart = restart
            self._set("stopping")
            self.core.log_app(("Restarting" if restart else "Stopping") + f" the server ({reason})")
            try:
                self.send("stop", source="orbisfox")
            except RuntimeError:
                pass
            proc = self.proc
        threading.Thread(target=self._stop_timer, args=(proc,), daemon=True).start()

    def _stop_timer(self, proc):
        timeout = int(self.core.settings["stop_timeout_s"])
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.core.log_app(f"The server did not stop within {timeout} seconds: ending the process.", level="warn")
            self._terminate(proc)

    def kill(self):
        with self._lock:
            if not self.alive:
                return
            self.cancel_pending(quiet=True)
            self._set("stopping")
            self.core.log_app("Force-stopping the server process.", level="warn")
            self._terminate(self.proc)

    @staticmethod
    def _terminate(proc):
        try:
            proc.kill()
        except OSError:
            pass

    # ----------------------------------------------------------------------------- countdowns
    def schedule(self, action, seconds, reason, message=None):
        """Stop or restart after warning the players. seconds=0 acts now."""
        if action not in ("stop", "restart"):
            raise ValueError("Unknown action.")
        if seconds <= 0 or not self.running or not self.core.players.online:
            self.stop(reason, restart=action == "restart")
            return
        self.cancel_pending(quiet=True)
        cancel = threading.Event()
        self._pending_cancel = cancel
        at = time.time() + seconds
        self.pending = {"action": action, "at": at, "reason": reason}
        self.core.publish_server()
        verb = "restarting" if action == "restart" else "shutting down"
        tmpl = message or ("Server " + verb + " in {time}.")
        self.core.log_app(f"Server {verb} in {seconds} s ({reason}).")
        threading.Thread(target=self._countdown, args=(action, at, reason, tmpl, cancel), daemon=True).start()

    def _countdown(self, action, at, reason, tmpl, cancel):
        marks = [600, 300, 120, 60, 30, 10, 5, 4, 3, 2, 1]
        total = at - time.time()
        warned = set(m for m in marks if m > total + 1)
        while not cancel.is_set():
            left = at - time.time()
            if left <= 0:
                break
            for m in marks:
                if m not in warned and left <= m + 0.5:
                    warned.add(m)
                    if self.running:
                        human = f"{m // 60} minute{'s' if m >= 120 else ''}" if m >= 60 else f"{m} second{'s' if m > 1 else ''}"
                        try:
                            msg = tmpl.replace("{time}", human)
                            self.say(msg, style="notify" if m >= 10 else "chat", level="warning")
                        except (RuntimeError, ValueError):
                            pass
            cancel.wait(0.25)
        if cancel.is_set():
            return
        self.pending = None
        self._pending_cancel = None
        self.stop(reason, restart=action == "restart")

    def cancel_pending(self, quiet=False):
        if self._pending_cancel:
            self._pending_cancel.set()
            self._pending_cancel = None
            had = self.pending
            self.pending = None
            self.core.publish_server()
            if had and not quiet:
                self.core.log_app(f"Cancelled the scheduled {had['action']}.")
                if self.running:
                    try:
                        self.say(f"The scheduled {had['action']} was cancelled.", style="notify", level="success")
                    except (RuntimeError, ValueError):
                        pass


def which(name):
    return shutil.which(name)
