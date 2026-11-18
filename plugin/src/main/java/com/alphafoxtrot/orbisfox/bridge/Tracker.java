package com.alphafoxtrot.orbisfox.bridge;

import com.hypixel.hytale.metrics.metric.HistoricMetric;
import com.hypixel.hytale.protocol.packets.connection.PongType;
import com.hypixel.hytale.server.core.io.PacketHandler;
import com.hypixel.hytale.server.core.universe.PlayerRef;
import com.hypixel.hytale.server.core.universe.Universe;
import com.hypixel.hytale.server.core.universe.world.World;
import com.hypixel.hytale.server.core.universe.world.commands.world.perf.WorldPerfCommand;

import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * Samples the server once a second. Player positions are copied on each world's own thread (the
 * world writes them in place); everything else is read from the sampler thread. A world whose
 * thread has not run the previous trivial task yet is "slow", and how long that task has waited
 * is reported as the world's latency: a stuck world shows a number that keeps growing.
 */
final class Tracker {

    static final String[] STAT_KEYS = {"distance", "deaths", "mob_kills", "player_kills", "chat_messages"};
    private static final PongType[] PING_TYPES = {PongType.Direct, PongType.Tick, PongType.Raw};
    private static final double MAX_STEP = 60.0;   // blocks per sample; more is a teleport

    final String run = UUID.randomUUID().toString();
    private final Map<UUID, Player> players = new ConcurrentHashMap<>();
    private final Map<UUID, double[]> stats = new ConcurrentHashMap<>();
    private final Map<String, WorldInfo> worlds = new ConcurrentHashMap<>();
    private final Map<String, Long> probes = new ConcurrentHashMap<>();       // world -> nanoTime sent
    private final Map<String, AtomicBoolean> copying = new ConcurrentHashMap<>();
    private final ArrayDeque<Map<String, Object>> events = new ArrayDeque<>();
    private long seq = 0;
    private ScheduledExecutorService exec;

    static final class Player {
        UUID uuid;
        String name;
        String world;
        double x, y, z;
        boolean hasPos;
        Integer ping;
        PlayerRef ref;
    }

    static final class WorldInfo {
        String name;
        double tps = -1, tickMs = -1;
        int targetTps, players, entities = -1, chunks = -1;
        long latencyMs;
    }

    void start() {
        exec = Executors.newSingleThreadScheduledExecutor(r -> {
            Thread t = new Thread(r, "OrbisFoxBridge-sampler");
            t.setDaemon(true);
            return t;
        });
        exec.scheduleAtFixedRate(this::sample, 1, 1, TimeUnit.SECONDS);
    }

    void stop() {
        if (exec != null) exec.shutdownNow();
    }

    // ------------------------------------------------------------------------------------ events
    void onJoin(UUID uuid, String name) {
        Player p = players.computeIfAbsent(uuid, u -> new Player());
        p.uuid = uuid;
        p.name = name;
        stats.computeIfAbsent(uuid, u -> new double[STAT_KEYS.length]);
    }

    void onLeave(UUID uuid) {
        players.remove(uuid);
    }

    void onChat(UUID uuid, String name, String message) {
        bump(uuid, 4, 1);
        Map<String, Object> ev = new LinkedHashMap<>();
        ev.put("type", "chat");
        ev.put("player", name);
        ev.put("uuid", uuid.toString());
        ev.put("message", message == null ? "" : message);
        event(ev);
    }

    void onPlayerDeath(UUID uuid, String name, String cause, String killer, String world, double x, double z) {
        bump(uuid, 1, 1);
        Map<String, Object> ev = new LinkedHashMap<>();
        ev.put("type", "death");
        ev.put("player", name);
        ev.put("uuid", uuid.toString());
        ev.put("cause", cause);
        if (killer != null) ev.put("killer", killer);
        if (world != null) ev.put("world", world);
        ev.put("x", Math.round(x));
        ev.put("z", Math.round(z));
        event(ev);
    }

    void onKill(UUID killerUuid, String killerName, String victim, boolean victimIsPlayer) {
        bump(killerUuid, victimIsPlayer ? 3 : 2, 1);
        Map<String, Object> ev = new LinkedHashMap<>();
        ev.put("type", "kill");
        ev.put("player", killerName);
        ev.put("uuid", killerUuid.toString());
        ev.put("victim", victim == null ? "something" : victim);
        event(ev);
    }

    private void bump(UUID uuid, int idx, double by) {
        double[] s = stats.computeIfAbsent(uuid, u -> new double[STAT_KEYS.length]);
        synchronized (s) {
            s[idx] += by;
        }
    }

    private synchronized void event(Map<String, Object> ev) {
        ev.put("seq", ++seq);
        ev.put("t", System.currentTimeMillis() / 1000.0);
        events.addLast(ev);
        while (events.size() > 300) events.removeFirst();
    }

    synchronized List<Map<String, Object>> eventsSince(long since) {
        List<Map<String, Object>> out = new ArrayList<>();
        for (Map<String, Object> ev : events) {
            if (((Long) ev.get("seq")) > since) out.add(ev);
        }
        return out;
    }

    // ------------------------------------------------------------------------------------ sampling
    private void sample() {
        try {
            Universe universe = Universe.get();
            if (universe == null) return;
            long now = System.nanoTime();
            for (World world : universe.getWorlds().values()) {
                if (world == null || !world.isAlive()) continue;
                String name = world.getName();
                WorldInfo info = worlds.computeIfAbsent(name, n -> new WorldInfo());
                info.name = name;
                readPerf(world, info);
                // Responsiveness probe.
                Long sent = probes.get(name);
                if (sent == null) {
                    probes.put(name, now);
                    try {
                        world.execute(() -> probes.remove(name));
                    } catch (RuntimeException notAccepting) {
                        probes.remove(name);
                    }
                    info.latencyMs = 0;
                } else {
                    info.latencyMs = TimeUnit.NANOSECONDS.toMillis(now - sent);
                }
                // Positions, copied on the world thread unless the previous copy is still waiting.
                AtomicBoolean busy = copying.computeIfAbsent(name, n -> new AtomicBoolean());
                if (world.getPlayerCount() > 0 && busy.compareAndSet(false, true)) {
                    try {
                        world.execute(() -> {
                            try {
                                copyPositions(world);
                            } finally {
                                busy.set(false);
                            }
                        });
                    } catch (RuntimeException notAccepting) {
                        busy.set(false);
                    }
                }
            }
            worlds.keySet().removeIf(n -> universe.getWorld(n) == null);
            for (Player p : players.values()) p.ping = ping(p.ref);
        } catch (Throwable t) {
            OrbisFoxBridge.LOG.fine("sample failed: " + t);
        }
    }

    private void readPerf(World world, WorldInfo info) {
        try {
            info.targetTps = world.getTps();
            long step = world.getTickStepNanos();
            HistoricMetric m = world.getBufferedTickLengthMetricSet();
            if (m != null) {
                double avg = m.getAverage(0);
                if (avg > 0) {
                    info.tps = Math.round(WorldPerfCommand.tpsFromDelta(avg, step) * 10) / 10.0;
                    info.tickMs = Math.round(avg / 1_000_00.0) / 10.0;
                }
            }
        } catch (Throwable ignored) {
            // tick metrics unavailable on this version
        }
        try {
            info.players = world.getPlayerCount();
        } catch (RuntimeException ignored) {
            info.players = -1;
        }
        try {
            info.entities = world.getEntityStore().getStore().getEntityCount();
        } catch (RuntimeException ignored) {
            info.entities = -1;
        }
        try {
            info.chunks = world.getChunkStore().getLoadedChunksCount();
        } catch (RuntimeException ignored) {
            info.chunks = -1;
        }
    }

    /** Runs on the world thread. */
    private void copyPositions(World world) {
        String wname = world.getName();
        for (PlayerRef ref : world.getPlayerRefs()) {
            if (ref == null) continue;
            var transform = ref.getTransform();
            if (transform == null) continue;
            var pos = transform.getPosition();
            if (pos == null) continue;
            UUID uuid = ref.getUuid();
            Player p = players.computeIfAbsent(uuid, u -> new Player());
            p.uuid = uuid;
            p.name = ref.getUsername();
            p.ref = ref;
            double x = pos.x, y = pos.y, z = pos.z;
            if (p.hasPos && wname.equals(p.world)) {
                double d = Math.hypot(x - p.x, z - p.z);
                if (d > 0.05 && d < MAX_STEP) bump(uuid, 0, d);
            }
            p.world = wname;
            p.x = x;
            p.y = y;
            p.z = z;
            p.hasPos = true;
        }
    }

    private Integer ping(PlayerRef ref) {
        if (ref == null) return null;
        try {
            PacketHandler handler = ref.getPacketHandler();
            if (handler == null) return null;
            for (PongType type : PING_TYPES) {
                PacketHandler.PingInfo info;
                try {
                    info = handler.getPingInfo(type);
                } catch (Throwable t) {
                    continue;
                }
                if (info == null) continue;
                HistoricMetric metric = info.getPingMetricSet();
                if (metric == null) continue;
                long raw = metric.getLastValue();
                if (raw <= 0) raw = (long) metric.getAverage(PacketHandler.PingInfo.ONE_MINUTE_INDEX);
                if (raw <= 0) continue;
                long ms = PacketHandler.PingInfo.TIME_UNIT.toMillis(raw);
                if (ms >= 0 && ms <= 60_000) return (int) ms;
            }
        } catch (Throwable ignored) {
            // no ping yet
        }
        return null;
    }

    // ------------------------------------------------------------------------------------ views
    List<Map<String, Object>> worldList() {
        List<Map<String, Object>> out = new ArrayList<>();
        for (WorldInfo w : worlds.values()) {
            Map<String, Object> m = new LinkedHashMap<>();
            m.put("name", w.name);
            m.put("tps", w.tps < 0 ? null : w.tps);
            m.put("target_tps", w.targetTps);
            m.put("tick_ms", w.tickMs < 0 ? null : w.tickMs);
            m.put("players", w.players);
            m.put("entities", w.entities);
            m.put("chunks", w.chunks);
            m.put("latency_ms", w.latencyMs);
            out.add(m);
        }
        return out;
    }

    List<Map<String, Object>> playerList() {
        List<Map<String, Object>> out = new ArrayList<>();
        for (Player p : players.values()) {
            Map<String, Object> m = new LinkedHashMap<>();
            m.put("uuid", p.uuid.toString());
            m.put("name", p.name);
            m.put("world", p.world);
            if (p.hasPos) {
                m.put("x", Math.round(p.x * 10) / 10.0);
                m.put("y", Math.round(p.y * 10) / 10.0);
                m.put("z", Math.round(p.z * 10) / 10.0);
            }
            m.put("ping_ms", p.ping);
            out.add(m);
        }
        return out;
    }

    List<Player> playersIn(String world) {
        List<Player> out = new ArrayList<>();
        for (Player p : players.values()) {
            if (p.hasPos && (world == null || world.isEmpty() || world.equals(p.world))) out.add(p);
        }
        return out;
    }

    Map<String, Object> statMap() {
        Map<String, Object> out = new LinkedHashMap<>();
        for (Map.Entry<UUID, double[]> e : stats.entrySet()) {
            Map<String, Object> m = new LinkedHashMap<>();
            double[] s = e.getValue();
            synchronized (s) {
                for (int i = 0; i < STAT_KEYS.length; i++) m.put(STAT_KEYS[i], Math.round(s[i] * 100) / 100.0);
            }
            out.put(e.getKey().toString(), m);
        }
        return out;
    }
}
