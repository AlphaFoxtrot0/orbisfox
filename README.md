<p align="center"><img src="assets/orbisfox-512.png" width="128" alt="OrbisFox"></p>

<h1 align="center">OrbisFox</h1>

<p align="center"><b>A modern control panel for Hytale dedicated servers.</b><br>
Live console, one-click restarts, backups, updates, whitelist and operators, player pings and stats,
an explored-world map and Memories page you can share on stream, and a watchdog that rescues a server
bogged down by too many loaded chunks.</p>

<p align="center"><img src="docs/img/overview.png" alt="OrbisFox: the console in the middle, server controls and live CPU, memory and tick rate on top, players with pings on the right"></p>

OrbisFox replaces the bare terminal window your Hytale server runs in. It starts the server
itself, so the console stays front and centre, but everything around it is a click away. Closing
the window does not stop the server; it keeps running until you stop it. You can open the panel
again from the desktop shortcut, or from your phone if you turn on network access.

No extra packages are needed: OrbisFox is plain Python. It works on Windows (with a native
app window) and on Linux (open it in a browser).

## Features

**Console**
- Live, colour-coded server output with filters (chat, players, problems) and search
- Command line with autocomplete for every vanilla command and player name, plus history
- Send as **Say** (chat), **Notice** (on-screen) or **Title** (big title on every screen)
- Quick buttons: who's online, save world, time of day, weather, announce, back up, unload idle chunks

**Server control**
- Start, stop and **restart** without closing the window, optionally after warning players (1 or 5 minutes)
- Force stop for a hung server; automatic restart after a crash (with crash-loop protection)
- Daily scheduled restarts with countdown warnings
- **Nobody online, no waiting:** every warning countdown is skipped when the server is empty, and a
  countdown finishes early if everyone leaves
- **Operators can veto** anything OrbisFox does on its own that would take the server down (scheduled
  restarts, watchdog recovery restarts, automatic updates): operators online get a private message and
  type **`/veto`** to cancel. That kind of action then waits 5 minutes (adjustable) before trying again
- Signs the server in to Hytale: shows the device code to enter when the server asks
- **Drop in an existing server:** OrbisFox reads its `start.bat`/`start.sh` (or any script that runs
  `HytaleServer.jar`), `jvm.options` and `config.json`, and starts it the same way: Java, memory, JVM flags,
  server options, port, backup and update settings. It refuses to start a second copy while the old
  start.bat window is still running it

**Players**
- Online players with **ping**, world, position and session time
- Kick, ban, unban, operator, whitelist, game mode, teleport, give item, locate
- **Stats**: playtime, sessions, **distance walked, deaths, mobs killed, PvP kills**, with a leaderboard
  and a sortable full table, plus a feed of recent deaths and kills. Playtime and sessions from before
  OrbisFox are read from the server's own logs; Hytale doesn't record the other stats, so they count
  from when OrbisFox (with its plugin) started watching

**Access**
- Whitelist on or off (as the running server reports it), add and remove players; operators, including
  players who are offline (OrbisFox finds their UUID, which Hytale needs); bans with reasons

**World**
- Day/night dial: dawn, midday, dusk, midnight, pause time
- Weather; view distance and player limit **live**; server name, message of the day, password, game mode

**Backups**
- One-click backup of the world, player data, server settings and (optionally) mods, saved first
  so the copy is consistent
- **Last backup** always visible in the top bar; automatic backups every N minutes; keep the newest N
- Restore with a safety backup made first; download any backup
- Also lists the server's own built-in backups (`--backup`)

**Updates**
- Checks Hytale's update channel and shows when a new server version is out
- **Installs it for you:** backs up, warns players, applies the update, restarts. It uses the server's
  built-in updater when the server is running, or Hytale's official downloader when it is stopped.
  Your world, settings and mods are never touched.
- Install with a 1-minute warning, or **install now** without one
- Release or pre-release channel; automatic installs when nobody is online, or even with players on
  (5-minute warning, operators can `/veto`)

**Performance**
- CPU and memory of the server process and of the whole PC (GPU optional); Java heap after garbage
  collection; time spent in GC; tick rate per world; loaded chunks
- Plain-language suggestions with an **Apply** button (memory size, view distance, safety options)
- **Watchdog** for the "too many chunks" freeze (see below)

**World map**
- Everything players have explored, drawn from the in-game map the server already makes, so it costs the
  server nothing. Your browser does the drawing
- Players on the map, plus a **Loaded chunks** overlay that shows where the server's memory is going
- **Share it** read-only on your network for a second screen, a phone or **OBS** (`&overlay=1`), or upload
  the map folder to any static web host. See [docs/world-map.md](docs/world-map.md)
- A **Memories** page on the same site: how many creature memories the server has restored, who is
  carrying which (and where they found them), and every memory nobody has found yet, with the **zones
  and biomes** the creature lives in, day or night, and whether you must defeat it. Read straight from
  the game files, no plugin needed. Link straight to it with `?view=memories`

**Also**
- Mods: list, enable and disable; past server logs viewer
- Open the panel from other devices on your network, behind a password
- Starts with Windows (optional), desktop and Start menu shortcuts
- **Keeps itself up to date:** when a new OrbisFox version is pushed to GitHub, the panel offers it.
  One click downloads it, restarts OrbisFox (stopping and restarting the server around it, with a warning
  if anyone is online) and keeps your data folder as it is. Or let it install on its own when nobody is online

## The "too many chunks" freeze

When players spread out and explore, the server can grind to a halt until it is restarted. That
happens because every loaded chunk lives in the server's memory. Once memory is nearly full, Java
spends its time collecting garbage instead of running the world.

OrbisFox watches for it: memory after garbage collection, time spent paused, the server's own
slow-tick warnings, and with the plugin, whether each world thread is still responding. When it
finds trouble it steps in gently, in this order:

1. Lowers the view distance while memory is tight, then raises it back once things are calm.
2. Asks the server to unload chunks nobody needs.
3. Warns players and restarts, if nothing else helps. Operators online can `/veto` it.

The full explanation, with what Hytale itself has fixed so far, is in
**[docs/chunk-freeze.md](docs/chunk-freeze.md)**.

<p align="center"><img src="docs/img/map.png" width="49%" alt="World map: explored terrain with player markers, and buttons to share the map">
<img src="docs/img/performance.png" width="49%" alt="Performance panel: health, heap after garbage collection, time in GC, loaded chunks, CPU and memory charts"></p>

<p align="center"><img src="docs/img/memories.png" width="80%" alt="Memories page of the map site: restored count, filters, and missing creatures grouped by category with their zones, biomes and time of day"></p>

## Getting started (Windows)

1. **Install Python 3.10 or newer** from https://www.python.org/downloads/ and tick *Add python.exe
   to PATH*. Your server PC already has Java 25 for Hytale; OrbisFox finds it.
2. **Download OrbisFox.** Use *Code → Download ZIP* on GitHub and unzip it somewhere permanent, such
   as `C:\OrbisFox`, or `git clone` it.
3. Run **`Install.bat`** to add desktop and Start menu shortcuts, then open **OrbisFox**.
4. Pick your server folder: the one that holds `HytaleServer.jar` (the `Server` folder of the
   official download). Paste its path, press **Browse** to pick it in OrbisFox's own folder browser, or
   let **Find servers on this PC** look for it. OrbisFox imports your existing setup from `start.bat`, `jvm.options` and
   `config.json` and shows what it found in Settings. **Close the old server window (start.bat)
   first**, then press **Start**.
5. Optional: open *Mods and logs* and press **Install plugin** to add the OrbisFox Bridge plugin,
   then restart the server.

The first time the server starts, it may ask to be signed in to Hytale. OrbisFox shows the link and
the code; afterwards press *Remember sign-in* so it stays signed in across restarts.

Want to look around first? Run **`Demo.bat`**. It starts OrbisFox with a pretend server, made-up
players, a fake map and made-up memories, in a separate data folder. Type `stress` in its console to
watch the watchdog deal with a chunk overload, `calm` to end it, and `mockveto` to play an operator
typing `/veto`.

### Linux

```sh
python3 -m orbisfox --background      # then open http://127.0.0.1:8765 in a browser
```

Run it under `systemd` or `tmux` to keep it going after you log out.

## The OrbisFox Bridge plugin

The server console doesn't show pings, kills, distance walked, tick rates, or which chunks are
loaded. The small **OrbisFox Bridge** plugin (`plugin/`) reports those to OrbisFox over
`127.0.0.1` only, and only when OrbisFox started the server. Each start uses a fresh random token.
It also adds the operators' **`/veto`** command and builds the **world map** from map images the
server has already drawn. Without the plugin, OrbisFox still works; those parts just show as
unavailable.

Install it from the panel (*Mods and logs → Install plugin*), or copy `OrbisFoxBridge.jar` from the
latest GitHub release into your server's `mods` folder. To build it yourself, see
[plugin/README.md](plugin/README.md).

## Using the panel from other devices

Settings → *Panel access*: set a password, turn on *Allow other devices on my network*, and restart
OrbisFox. Then browse to `http://<server-PC-IP>:8765` from a phone or another PC.

Do **not** forward the panel's port on your router. For access from outside your home, put it
behind a VPN or a sign-in proxy such as Cloudflare Tunnel with Cloudflare Access.

## Where things are kept

Everything OrbisFox stores lives in its `data` folder, next to the program. That includes its
settings, backups (unless you choose another folder), player stats, the map and its own log. Updating
OrbisFox never touches it. The Hytale server's own files stay where they are.

| | |
|---|---|
| Panel | `http://127.0.0.1:8765` (this PC only, unless you allow your network) |
| Settings | `data/settings.json` |
| Backups | `data/backups/orbisfox-<date>-<reason>.zip` |
| Stats | `data/players.json` |
| GC log | `<server>/logs/orbisfox-gc.log` |

## Troubleshooting

- **"Java was not found" / "needs Java 25":** install Java 25 (for example Eclipse Temurin 25), or
  set its path in Settings.
- **The server starts, but players can't join:** check the notice at the top of the console. The
  server may need to be signed in. Also forward **UDP** port 5520 on your router.
- **"A server from an earlier OrbisFox session is still running":** OrbisFox was closed in a way
  that left the server running without its console. Press *Stop it* to take over.
- **Lots of browser windows open when OrbisFox starts:** OrbisFox opens one app-mode window using
  Chrome if you have it, otherwise Edge, with its own profile so first-run pages don't appear. If you
  prefer a normal browser tab, choose it in Settings → *Starting and stopping* → *Open OrbisFox in*.
- **Changing config.json by hand:** stop the server first; it rewrites its settings while running.
  The World panel handles this for you: view distance and player limit change live, and the rest is
  saved and written just before the server next starts.
- **The shared map (or the panel) times out on another device:** Windows Firewall is blocking it,
  often because its "Allow access?" prompt was dismissed. The map window and Settings show a warning
  with **Allow through firewall**; approve Windows' prompt on the server PC. The rule only admits
  devices on your local network.
- **`/veto` says the command doesn't exist:** the OrbisFox Bridge plugin isn't loaded. Install it
  (*Mods and logs → Install plugin*) and restart the server; plugins only load at start. Settings →
  *Operator veto* shows whether it's connected.
- **"Slow tick" lines after a backup:** saving the world can make one chunk miss its 33 ms tick
  while Java frees memory. One now and then is harmless; the watchdog only reacts to a steady stream.

## Development

```
orbisfox/          Python backend (standard library only)
  core.py          ties everything together; console parsing; scheduler
  server.py        runs the Java process: start, stop, restart, countdowns, exit code 8 updates
  hytale.py        Hytale specifics: log format, files, commands, versions
  watchdog.py      the chunk-freeze watchdog and suggestions
  jvm.py           GC log reader, finding Java
  metrics.py       CPU, memory and GPU (Windows performance counters / Linux /proc)
  veto.py          operators' /veto and the cool-down
  importer.py      reads an existing server's start script, jvm.options and config.json
  memories.py      memories from Assets.zip and the save files
  worldmap.py      the map folder, its viewer and the read-only network share
  selfupdate.py    OrbisFox updating itself from GitHub
  fsbrowse.py      the in-app folder browser and server finder
  firewall.py      checks and opens Windows Firewall for the map share and network access
  backups.py  updates.py  players.py  bridge.py  routes.py  httpd.py  desktop.py
web/               the panel (plain HTML, CSS and ES modules; no build step)
web/livemap/       the standalone world-map viewer (copied into the map folder)
plugin/            OrbisFox Bridge, the Java server plugin
tools/mock_server.py   pretend Hytale server used by the demo and for testing
```

Run from source with `python -m orbisfox` (add `--demo` for the pretend server). The panel talks to
a JSON API under `/api/` and a Server-Sent Events stream at `/api/events`.

## Credits

- Made by AlphaFoxtrot. The UI follows the AlphaFoxtrot design system: fox orange, circuit cyan and
  hexagons on near-black teal, with a Hytale-style chamfered frame.
- The OrbisFox logo (a faceted fox over a voxel Orbis in a gold hexagon) was drawn for OrbisFox;
  its source is `assets/orbisfox.svg`.
- [Genos](https://fonts.google.com/specimen/Genos) typeface, SIL Open Font License 1.1 (`web/fonts/OFL.txt`).
- Command, log and file formats come from the official
  [Hytale Server Manual](https://support.hytale.com/hc/en-us/articles/45326769420827-Hytale-Server-Manual)
  and from how existing open-source Hytale panels and plugins use them.

OrbisFox is a fan-made tool. It is not affiliated with or endorsed by Hypixel Studios. Hytale is a
trademark of Hypixel Studios.

## License

The code and the OrbisFox logo are released under the MIT License (see [LICENSE](LICENSE)). The
AlphaFoxtrot hexagon texture (`web/img/hexgrid.jpg`) is © AlphaFoxtrot and may be used only as part of
OrbisFox.
