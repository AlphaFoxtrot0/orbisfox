package com.alphafoxtrot.orbisfox.bridge;

import com.hypixel.hytale.server.core.universe.Universe;
import com.hypixel.hytale.server.core.universe.world.World;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;

import java.io.IOException;
import java.io.OutputStream;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.Executors;

/** A tiny JSON API on 127.0.0.1 for the OrbisFox panel. Every request must carry the token. */
final class BridgeServer {

    private final HttpServer server;
    private final byte[] token;
    private final Tracker tracker;
    private final Chunks chunks = new Chunks();

    BridgeServer(int port, String token, Tracker tracker) throws IOException {
        this.token = token.getBytes(StandardCharsets.UTF_8);
        this.tracker = tracker;
        server = HttpServer.create(new InetSocketAddress(InetAddress.getLoopbackAddress(), port), 16);
        server.setExecutor(Executors.newFixedThreadPool(3, r -> {
            Thread t = new Thread(r, "OrbisFoxBridge-http");
            t.setDaemon(true);
            return t;
        }));
        server.createContext("/", this::handle);
    }

    void start() {
        server.start();
    }

    void stop() {
        server.stop(0);
    }

    private void handle(HttpExchange ex) throws IOException {
        try {
            String got = ex.getRequestHeaders().getFirst("X-OrbisFox-Token");
            if (got == null || !MessageDigest.isEqual(got.getBytes(StandardCharsets.UTF_8), token)) {
                send(ex, 401, "application/json", "{\"error\":\"token\"}".getBytes(StandardCharsets.UTF_8));
                return;
            }
            Map<String, String> q = query(ex.getRequestURI().getRawQuery());
            switch (ex.getRequestURI().getPath()) {
                case "/status" -> json(ex, status(parseLong(q.get("since"))));
                case "/chunks" -> json(ex, chunkList(q.get("world")));
                case "/tile" -> {
                    World w = world(q.get("world"));
                    byte[] png = w == null ? null : chunks.tile(w, (int) parseLong(q.get("x")), (int) parseLong(q.get("z")));
                    if (png == null) send(ex, 404, "text/plain", new byte[0]);
                    else send(ex, 200, "image/png", png);
                }
                default -> send(ex, 404, "text/plain", new byte[0]);
            }
        } catch (Throwable t) {
            send(ex, 500, "application/json", Json.write(Map.of("error", String.valueOf(t))).getBytes(StandardCharsets.UTF_8));
        }
    }

    private Map<String, Object> status(long since) {
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("version", OrbisFoxBridge.VERSION);
        out.put("run", tracker.run);
        out.put("t", System.currentTimeMillis() / 1000.0);
        out.put("worlds", tracker.worldList());
        out.put("players", tracker.playerList());
        out.put("stats", tracker.statMap());
        out.put("events", tracker.eventsSince(since));
        Runtime rt = Runtime.getRuntime();
        out.put("heap", Map.of("used", rt.totalMemory() - rt.freeMemory(), "max", rt.maxMemory()));
        return out;
    }

    private Map<String, Object> chunkList(String worldName) {
        World w = world(worldName);
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("chunk_size", 32);
        if (w == null) {
            out.put("world", worldName);
            out.put("chunks", List.of());
            return out;
        }
        out.put("world", w.getName());
        Chunks.Result r = chunks.loaded(w, tracker.playersIn(w.getName()));
        out.put("chunks", r.chunks());
        out.put("exact", r.exact());
        out.put("chunk_size", r.chunkSize());
        List<Map<String, Object>> ps = new java.util.ArrayList<>();
        for (Tracker.Player p : tracker.playersIn(w.getName())) {
            ps.add(Map.of("name", p.name == null ? "?" : p.name, "uuid", p.uuid.toString(), "x", p.x, "z", p.z));
        }
        out.put("players", ps);
        return out;
    }

    private static World world(String name) {
        Universe u = Universe.get();
        if (u == null) return null;
        if (name != null && !name.isBlank()) return u.getWorld(name);
        World def = null;
        for (World w : u.getWorlds().values()) {
            if (def == null || w.getPlayerCount() > def.getPlayerCount()) def = w;
        }
        return def;
    }

    private static void json(HttpExchange ex, Object body) throws IOException {
        send(ex, 200, "application/json", Json.write(body).getBytes(StandardCharsets.UTF_8));
    }

    private static void send(HttpExchange ex, int status, String type, byte[] body) throws IOException {
        ex.getResponseHeaders().set("Content-Type", type);
        ex.sendResponseHeaders(status, body.length == 0 ? -1 : body.length);
        if (body.length > 0) {
            try (OutputStream os = ex.getResponseBody()) {
                os.write(body);
            }
        }
        ex.close();
    }

    private static Map<String, String> query(String raw) {
        Map<String, String> m = new HashMap<>();
        if (raw == null) return m;
        for (String part : raw.split("&")) {
            int i = part.indexOf('=');
            if (i > 0) m.put(URLDecoder.decode(part.substring(0, i), StandardCharsets.UTF_8),
                    URLDecoder.decode(part.substring(i + 1), StandardCharsets.UTF_8));
        }
        return m;
    }

    private static long parseLong(String s) {
        try {
            return s == null ? 0 : Long.parseLong(s.trim());
        } catch (NumberFormatException e) {
            return 0;
        }
    }
}
