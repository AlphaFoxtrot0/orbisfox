# Why the server stalls when players spread out, and what OrbisFox does about it

**Symptom:** everyone is fine while playing near each other. Then a few players head off in
different directions, often flying or riding into new terrain. The game starts rubber-banding,
blocks take seconds to break, commands lag, and eventually nothing responds until the server is
restarted.

## What is going on

Every loaded chunk (32 × 32 blocks) lives in the server's Java heap. The server keeps a circle of
chunks loaded around every player, out to the view distance (`MaxViewRadius`). Hytale's default is 32
chunks; Hytale recommends 12.

- **Players together share one circle. Players apart each load their own.** Four players at a
  12-chunk radius in four different places hold four times the chunks of four players standing
  together. Memory grows with the *square* of the view distance, per separate area.
- **New terrain costs more than old terrain.** Generating a chunk allocates far more temporary
  memory and CPU than loading a saved one. Flying through unexplored land generates chunks fast.
- **Chunks have not always unloaded promptly.** Community reports have shown a server holding
  thousands more chunks than players could see. Hytale's patch notes have since fixed several
  related problems; see the table below.
- **When the heap is nearly full, Java's garbage collector runs back to back.** Each collection
  pauses the world. The server is "alive", but it spends most of its time cleaning up instead of
  ticking. The official server manual says it plainly: *"A typical symptom of memory pressure is
  increased CPU usage due to garbage collection."*
- **Each world ticks on one thread.** A world that falls behind stays behind. The server prints
  warnings such as:

  ```
  [2026/01/25 21:16:03 SEVERE] [World|default] Took too long to run pre-load process hook for chunk: 2sec 63ms ... > TICK_STEP
  ```

That is the "basically stops working" state. Restarting helps because it empties the heap.

## What Hytale has fixed (from the official patch notes)

| Version | Fix relevant to this |
|---|---|
| Update 3 (Feb 2026) | Chunk-saving deadlock fixed when a world is removed; "multiple memory leaks" fixed |
| Update 6 (Aug 2026) | Fixed a "server freeze that could occur during chunk generation or loading"; lower memory use at high view distances; added the `maxviewradius` command |
| 0.6.5 (Sep 2026) | Fixed unloaded worlds staying in memory after their world maps were generated |
| Update 7 (Oct 2026) | Chunk saves use a bounded buffer pool, so busy servers no longer run past the direct-memory limit; a world tick no longer waits on a save |
| 0.7.3 (Oct 2026) | World generation uses fewer threads on 4-, 6- and 7-thread CPUs; "exploring new terrain causes less lag" |
| Update 8 (pre-release, Nov 2026) | "Fixed an issue where the server could run out of memory when flying through new terrain, causing stalling"; lower chunk sections unload on their own; two memory leaks fixed |

**The single most useful thing you can do is stay up to date.** OrbisFox's Updates panel shows when
a new version is out and installs it for you. Update 8 is aimed squarely at this problem: use the
pre-release channel to get it early, or wait for the release.

## What OrbisFox does

### Prevention (Performance panel → Suggestions, Settings)

- **Memory sized for how you play.** OrbisFox suggests a heap size from your player count and your
  PC's RAM: 6 GB for up to 3 players, 8 GB for 4 to 6, more beyond that, never more than about 60%
  of the PC's memory.
- **View distance at 12 chunks** unless you have memory to spare. You can change it live from the
  World panel; OrbisFox uses the server's `maxviewradius` command, so no restart is needed.
- **`-XX:+ExitOnOutOfMemoryError`.** A server that runs out of memory exits, and OrbisFox restarts it,
  instead of hanging in a half-dead state.
- **G1 garbage collector with a 200 ms pause goal**, unless you choose another collector.

### Detection (always on while the server runs)

- **GC log.** OrbisFox starts Java with a small, rotated GC log (`logs/orbisfox-gc.log`) and reads it.
  It tracks how full the heap really is after each collection and what share of time the server
  spends paused.
- **The server's own warnings** about slow ticks and world threads that stopped accepting work.
- **With the OrbisFox Bridge plugin:** each world's tick rate, its loaded-chunk count, and a
  responsiveness probe. Every second the plugin hands each world a trivial task and measures how
  long it waits. A stuck world shows a number that keeps climbing, which catches a freeze within
  seconds.

### Recovery (the watchdog, in order)

1. **Under strain** (heap more than 78% full after collection, more than 12% of time in GC, slow
   ticks, or tick rate below 70%): OrbisFox **lowers the view distance by 2 chunks** at a time, down
   to 6, so distant chunks unload. When things have been calm for 10 minutes it raises it back step
   by step. The value you configured is restored when the server stops.
2. **Critical** (heap at least 92% full after collection, at least 40% of time in GC, a world thread
   stuck, or many ticks over a second): OrbisFox asks the server to **unload chunks nobody needs**
   (`chunk unload`).
3. **Still critical for 2 minutes:** OrbisFox **warns the players (60 seconds) and restarts the
   server**, after saving the world. Operators online can `/veto` it. It restarts at most
   once per 15 minutes and at most 3 times an hour. After that it alerts you instead.

Every threshold can be changed in Settings → Watchdog. Each step is logged in the console and in
Performance → Watchdog.

### Seeing it: the loaded-chunks overlay

Open the **World map** and tick **Loaded chunks**. It shows every chunk currently in the server's
memory on top of the explored map. When the server struggles you will usually see several large,
separate patches, one per wandering player. Once you can see it, you can do something about it: ask
players to regroup, lower the view distance, or press *Unload idle chunks*.

### Operators can say no

Every automatic recovery restart (and scheduled restart, and automatic update) is announced privately to
operators who are online, and they can type `/veto` to cancel it, for example while fighting a boss.
OrbisFox then leaves that kind of action alone for 5 minutes (adjustable in Settings) before trying
again. With nobody online there is no warning and no wait.

## Other things that help

- **Pre-generate the area around spawn** while nobody is online, so exploring near spawn loads
  saved chunks instead of generating new ones. Hosting guides describe the command as
  `world settings pregenerate set <minX> <minZ> <maxX> <maxZ> --world <name>` followed by a restart.
  Check `world settings --help` on your version first.
- **Limit world-generation threads** on CPUs with few cores: `worldgen2 concurrency <level>` (0 resets
  it).
- **Separate worlds tick on separate threads.** If your group splits into regular sub-groups,
  giving each its own world spreads the load. This follows from the official "one thread per
  world" design; no official source recommends it outright.

## Sources

- Hytale Server Manual: https://support.hytale.com/hc/en-us/articles/45326769420827-Hytale-Server-Manual
- Patch notes: https://hytale.com/news (Update 3, 6, 7 and its hotfixes, Update 8 pre-release)
- Community reports of lingering chunks and tick warnings: https://github.com/John-Willikers/hyfixes ,
  https://github.com/pterodactyl/game-eggs/issues/409
- Hosting guidance on memory per player: WinterNode, Shockbyte, BisectHosting knowledge bases
