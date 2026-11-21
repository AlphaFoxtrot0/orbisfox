# The world map: explored area, streaming and sharing

## How it's made, and why it doesn't slow the server down

Hytale already draws a map image for every chunk near a player, for the in-game map. The OrbisFox
Bridge plugin **copies those finished images** on a low-priority thread every 30 seconds. It doesn't
ask the server to render anything extra, and it never loads or generates terrain to draw the map. It
stitches the images into region tiles (16 × 16 chunks, 512 × 512 blocks) at three zoom levels and saves
them as small PNG files in OrbisFox's map folder (`data/map` by default).

The map you see is **drawn by your browser**, from those files. The server PC just hands files out,
which is about the cheapest thing a computer can do. Open it on another PC or in OBS and that PC does
the drawing.

The map covers everywhere players have been since the plugin was installed. To fill in areas explored
earlier, turn on *Settings → World map → Fill in areas explored before OrbisFox*. It works through those
old chunks a dozen at a time, and **only while nobody is online**, because the server has to load them
to draw them.

## Viewing it

- **In OrbisFox:** the map button on the right rail. Tick *Loaded chunks* to see which chunks are in the
  server's memory right now, a quick way to spot players spread out across the world.
- **In a browser on the server PC:** *Open in browser* in the map window, or `http://127.0.0.1:8765/map/`.

## On another PC, a phone, or your stream

1. *Settings → World map → Share the map on my network*, then **Save**.
2. Copy the link from the map window (*Copy share link*). It looks like `http://192.168.1.20:8767/`.

The share runs on its own port and serves **only** the map: no panel, no controls, no console. You can
set a *Map key*; viewers then need `?key=...` in the link (the copied link includes it).

### OBS

Add a **Browser** source with the share link, plus options after `?` (join them with `&`):

| Option | What it does |
|---|---|
| `overlay=1` | hides every button and label, just the map |
| `bg=transparent` | transparent outside the explored land, for layering over your scene |
| `follow=PlayerName` | keeps that player in the middle |
| `x=100&z=-250&zoom=0.5` | starting position (blocks) and zoom (screen pixels per block) |
| `players=0` | hides player markers (to stop stream snipers seeing where you are) |
| `world=name` | a specific world |
| `refresh=120` | look for new tiles every 120 seconds (default 60) |

Example: `http://192.168.1.20:8767/?overlay=1&follow=Trey&zoom=0.8`

Player positions update every 10 seconds. Turn them off entirely in *Settings → World map → Show
players on the map*.

### Away from home

Don't forward the map port on your router as-is. Two easy options:

- **Upload the folder.** The map folder is a complete static website (`index.html`, the viewer, tiles
  and JSON). Copy it to GitHub Pages, Cloudflare Pages, Netlify or any web host. It shows the map as of
  when you copied it; player positions only work on the live share.
- **Tunnel the share** with a service you already use (for example a Cloudflare Tunnel to port 8767,
  behind Cloudflare Access if you want a sign-in).

## Files in the map folder

```
index.html, livemap.js, livemap.css, fonts/, img/   the viewer (copied in by OrbisFox)
worlds.json                                        worlds that have tiles
players.json, status.json                          live extras, rewritten every 10 s
<world>/index.json                                 list of region tiles
<world>/1/<rx>_<rz>.png                            full detail (1 pixel per block)
<world>/4/<rx>_<rz>.png, <world>/16/...            zoomed-out versions
<world>/captured.bin                               which chunks are already drawn (plugin bookkeeping)
```
