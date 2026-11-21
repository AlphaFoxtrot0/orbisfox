"""Start OrbisFox:  python -m orbisfox  [--background] [--port N] [--no-window]
                    [--install-shortcuts | --remove-shortcuts]

Only one OrbisFox runs per PC: starting it again just opens another window on the running one.
Closing the window leaves OrbisFox (and the Hytale server) running; use Quit in the panel's menu
to stop both.
"""
import argparse
import json
import os
import signal
import sys
import threading
import urllib.request

from . import __version__, desktop
from .settings import DATA_DIR, Settings


def _running_instance(port):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/hello", timeout=1.5) as r:
            return json.load(r).get("app") == "OrbisFox"
    except (OSError, ValueError):
        return False


def _prepare_demo(data_dir):
    from . import demo
    demo.prepare(data_dir)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="orbisfox", description="OrbisFox - Hytale server manager")
    ap.add_argument("--background", action="store_true", help="start without opening a window")
    ap.add_argument("--no-window", action="store_true", help="same as --background")
    ap.add_argument("--port", type=int, help="panel port (default from settings, 8765)")
    ap.add_argument("--data", help="data folder (default: ./data)")
    ap.add_argument("--demo", action="store_true",
                    help="try OrbisFox with a pretend server (separate data folder, port 8770)")
    ap.add_argument("--after-update", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--install-shortcuts", action="store_true")
    ap.add_argument("--remove-shortcuts", action="store_true")
    ap.add_argument("--version", action="version", version=f"OrbisFox {__version__}")
    args = ap.parse_args(argv)

    if args.install_shortcuts:
        for p in desktop.make_shortcuts():
            print("Created", p)
        return 0
    if args.remove_shortcuts:
        desktop.remove_shortcuts()
        print("Removed OrbisFox shortcuts.")
        return 0

    data_dir = args.data or os.environ.get("ORBISFOX_DATA") or DATA_DIR
    if args.demo:
        data_dir = args.data or os.path.join(os.path.dirname(DATA_DIR), "data-demo")
        _prepare_demo(data_dir)
        args.port = args.port or 8770
    os.makedirs(data_dir, exist_ok=True)
    if sys.stderr is None or sys.executable.lower().endswith("pythonw.exe"):
        log = open(os.path.join(data_dir, "orbisfox-errors.log"), "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stderr = log

    port = args.port or int(Settings(os.path.join(data_dir, "settings.json"))["panel"].get("port", 8765))
    show = not (args.background or args.no_window)
    url = f"http://127.0.0.1:{port}/"
    if args.after_update:
        # The old OrbisFox is still shutting down: wait for it to let go of the port.
        import time
        for _ in range(60):
            if not _running_instance(port):
                break
            time.sleep(0.5)
    if _running_instance(port):
        if show:
            desktop.open_window(url, data_dir)
        return 0

    from .core import Core
    from .httpd import PanelServer
    from .routes import router

    core = Core(data_dir)
    panel = PanelServer(core, router)
    lan = bool(core.settings["panel"].get("lan")) and bool(core.settings["panel"].get("password_hash"))
    try:
        panel.start(port, lan)
    except OSError as e:
        print(f"OrbisFox could not open port {port}: {e}", file=sys.stderr)
        return 1
    core.log_app(f"OrbisFox {__version__} is running at {url}" + (" (also on your network)" if lan else ""))
    core.argv = list(argv if argv is not None else sys.argv[1:])
    core.start_threads()
    from .selfupdate import resume_server_if_asked
    threading.Thread(target=resume_server_if_asked, args=(core,), daemon=True).start()
    if show:
        desktop.open_window(url, data_dir)
    else:
        print(f"OrbisFox is running at {url}")

    stop = threading.Event()

    def on_signal(*_):
        stop.set()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, on_signal)
        except (ValueError, OSError):
            pass
    try:
        while not stop.wait(1):
            pass
    finally:
        core.shutdown()
        panel.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
