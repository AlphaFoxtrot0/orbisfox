# OrbisFox Bridge (server plugin)

A small Hytale server plugin that tells the OrbisFox panel what the console can't show:

| What | How |
|---|---|
| Players' position, world and **ping** | copied on each world's thread once a second; ping from the connection's ping metrics |
| **Distance walked** | sum of horizontal movement between samples (teleports ignored) |
| **Deaths, mobs killed, PvP kills** | an entity death system: the victim, the cause and who dealt the blow |
| Chat messages | `PlayerChatEvent` (after other mods had their say) |
| **Tick rate** and tick time per world | the world's own tick-length metrics, same as `world perf` |
| Loaded chunks, entities per world | the world's chunk and entity stores |
| **World responsiveness** | every second each world gets a trivial task; the time it waits is reported. A stuck world shows a number that keeps climbing |
| **Loaded chunk list and map images** | the chunk store and the server's world-map renderer, for loaded chunks only, so the map never loads or generates terrain |

It listens on **127.0.0.1 only** and only when the server was started by OrbisFox. OrbisFox passes a
port and a fresh random token in the environment (`ORBISFOX_BRIDGE_PORT`, `ORBISFOX_BRIDGE_TOKEN`), and
every request must carry the token. On a server started any other way the plugin does nothing.

## Install

From the OrbisFox panel: *Mods and logs → Install plugin*, then restart the server. Or copy
`OrbisFoxBridge.jar` (from a GitHub release, or `plugin/dist/` after building) into the server's
`mods` folder.

## Build

You need a JDK 25.

With Gradle (fetches the server API from Hytale's public Maven repository):

```sh
cd plugin
gradle build                                   # -> plugin/dist/OrbisFoxBridge.jar
gradle build -PhytaleVersion=0.8.0-pre.5       # build against another server version
gradle build -PhytaleServerJar=/path/to/HytaleServer.jar
```

Without Gradle, against your server's own jar:

```powershell
.\build.ps1 -ServerJar "C:\Hytale\Server\HytaleServer.jar"
```
```sh
./build.sh /path/to/HytaleServer.jar
```

## API (for the curious)

All responses are JSON unless noted. Every request needs the header `X-OrbisFox-Token`.

- `GET /status?since=<seq>`: `version`, `run` (changes each start), `worlds[]` (`name`, `tps`,
  `target_tps`, `tick_ms`, `players`, `entities`, `chunks`, `latency_ms`), `players[]` (`uuid`, `name`,
  `world`, `x`, `y`, `z`, `ping_ms`), `stats{uuid: {distance, deaths, mob_kills, player_kills,
  chat_messages}}` (counted since this start), `events[]` newer than `since` (`death`, `kill`, `chat`),
  and `heap` (`used`, `max` bytes).
- `GET /chunks?world=<name>`: `chunks` as `[x, z, state]` triples (chunk coordinates, 32 blocks per
  chunk), `exact` (false when this server version offers no loaded-chunk list and the plugin falls
  back to players' view areas), and `players`.
- `GET /tile?world=<name>&x=<cx>&z=<cz>`: a PNG of that chunk from the server's world map. Returns
  404 for chunks that aren't loaded.
