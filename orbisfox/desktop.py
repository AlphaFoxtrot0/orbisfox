"""Desktop integration: the app window, shortcuts and starting with Windows."""
import os
import shutil
import subprocess
import sys
import webbrowser

from .settings import APP_DIR, DATA_DIR

NO_WINDOW = 0x08000000 if os.name == "nt" else 0
ICON = os.path.join(APP_DIR, "assets", "orbisfox.ico")


def _browser():
    """Edge (on every Windows 11 PC) or Chrome, for a chromeless app window."""
    cands = []
    if os.name == "nt":
        for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"), os.environ.get("LOCALAPPDATA")):
            if base:
                cands += [os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe"),
                          os.path.join(base, "Google", "Chrome", "Application", "chrome.exe")]
    else:
        cands += [shutil.which(n) for n in ("microsoft-edge", "google-chrome", "chromium", "chromium-browser")]
    return next((c for c in cands if c and os.path.isfile(c)), None)


def open_window(url, data_dir=None):
    exe = _browser()
    if not exe:
        webbrowser.open(url)
        return
    profile = os.path.join(data_dir or DATA_DIR, "window")
    os.makedirs(profile, exist_ok=True)
    subprocess.Popen([exe, f"--app={url}", f"--user-data-dir={profile}", "--window-size=1500,940",
                      "--no-first-run", "--no-default-browser-check", "--disable-features=Translate"],
                     creationflags=NO_WINDOW, close_fds=True)


def _pythonw():
    exe = sys.executable
    if os.name == "nt" and os.path.basename(exe).lower() == "python.exe":
        w = os.path.join(os.path.dirname(exe), "pythonw.exe")
        if os.path.isfile(w):
            return w
    return exe


def _shortcut(path, args, description):
    ps = (
        "$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:OF_LNK);"
        "$s.TargetPath=$env:OF_TARGET;$s.Arguments=$env:OF_ARGS;$s.WorkingDirectory=$env:OF_DIR;"
        "$s.IconLocation=$env:OF_ICON;$s.Description=$env:OF_DESC;$s.Save()"
    )
    env = dict(os.environ, OF_LNK=path, OF_TARGET=_pythonw(), OF_ARGS=args, OF_DIR=APP_DIR,
               OF_ICON=ICON, OF_DESC=description)
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], env=env,
                   capture_output=True, creationflags=NO_WINDOW, check=False)


def startup_link():
    return os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows", "Start Menu", "Programs",
                        "Startup", "OrbisFox.lnk")


def set_startup(on):
    if os.name != "nt":
        return
    link = startup_link()
    if on:
        _shortcut(link, f'"{os.path.join(APP_DIR, "orbisfox.pyw")}" --background',
                  "Start OrbisFox (Hytale server manager) in the background")
    elif os.path.exists(link):
        os.remove(link)


def make_shortcuts():
    if os.name != "nt":
        return []
    made = []
    target = f'"{os.path.join(APP_DIR, "orbisfox.pyw")}"'
    desktop = os.path.join(os.environ.get("USERPROFILE", ""), "Desktop", "OrbisFox.lnk")
    menu = os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows", "Start Menu", "Programs", "OrbisFox.lnk")
    for link in (desktop, menu):
        if os.path.isdir(os.path.dirname(link)):
            _shortcut(link, target, "OrbisFox - Hytale server manager")
            made.append(link)
    return made


def remove_shortcuts():
    for link in (os.path.join(os.environ.get("USERPROFILE", ""), "Desktop", "OrbisFox.lnk"),
                 os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows", "Start Menu", "Programs",
                              "OrbisFox.lnk"),
                 startup_link()):
        if os.path.exists(link):
            os.remove(link)
