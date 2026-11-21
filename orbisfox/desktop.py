"""Desktop integration: the app window, shortcuts and starting with Windows."""
import os
import shutil
import subprocess
import sys
import time
import webbrowser

from .settings import APP_DIR, DATA_DIR, Settings

NO_WINDOW = 0x08000000 if os.name == "nt" else 0
ICON = os.path.join(APP_DIR, "assets", "orbisfox.ico")


def _browser():
    """Chrome if installed (already set up on most PCs, and it honours --no-first-run), else Edge,
    for a chromeless app window."""
    chrome, edge = [], []
    if os.name == "nt":
        for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"), os.environ.get("LOCALAPPDATA")):
            if base:
                chrome.append(os.path.join(base, "Google", "Chrome", "Application", "chrome.exe"))
                edge.append(os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe"))
    else:
        chrome += [shutil.which(n) for n in ("google-chrome", "chromium", "chromium-browser")]
        edge += [shutil.which("microsoft-edge")]
    return next((c for c in chrome + edge if c and os.path.isfile(c)), None)


def open_window(url, data_dir=None):
    """Opens the panel. Settings "window": "app" (default, a chromeless browser window), "browser"
    (a tab in your normal browser). Use --background to start without a window. Repeat requests
    within a few seconds are ignored, so double-clicking the shortcut doesn't stack windows."""
    data_dir = data_dir or DATA_DIR
    mode = Settings(os.path.join(data_dir, "settings.json")).get("window", "app")
    stamp = os.path.join(data_dir, "window.opened")
    try:
        if time.time() - os.path.getmtime(stamp) < 6:
            return
    except OSError:
        pass
    try:
        os.makedirs(data_dir, exist_ok=True)
        with open(stamp, "w") as f:
            f.write(str(time.time()))
    except OSError:
        pass
    exe = _browser() if mode == "app" else None
    if not exe:
        webbrowser.open(url)
        return
    profile = os.path.join(data_dir, "window")
    os.makedirs(profile, exist_ok=True)
    # A "First Run" file tells Chromium-based browsers this profile is already set up, so they skip
    # their welcome and sign-in windows.
    try:
        open(os.path.join(profile, "First Run"), "a").close()
    except OSError:
        pass
    subprocess.Popen([exe, f"--app={url}", f"--user-data-dir={profile}", "--window-size=1500,940",
                      "--no-first-run", "--no-default-browser-check", "--disable-sync", "--disable-features=Translate",
                      "--hide-crash-restore-bubble"],
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
