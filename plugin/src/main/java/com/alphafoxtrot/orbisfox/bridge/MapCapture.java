package com.alphafoxtrot.orbisfox.bridge;

import com.hypixel.hytale.math.util.ChunkUtil;
import com.hypixel.hytale.protocol.packets.worldmap.MapImage;
import com.hypixel.hytale.server.core.universe.Universe;
import com.hypixel.hytale.server.core.universe.world.World;
import com.hypixel.hytale.server.core.universe.world.chunk.palette.BitFieldArr;
import com.hypixel.hytale.server.core.universe.world.storage.IChunkLoader;
import com.hypixel.hytale.server.core.universe.world.worldmap.WorldMapManager;
import it.unimi.dsi.fastutil.longs.Long2LongOpenHashMap;
import it.unimi.dsi.fastutil.longs.LongIterator;
import it.unimi.dsi.fastutil.longs.LongList;
import it.unimi.dsi.fastutil.longs.LongOpenHashSet;

import javax.imageio.ImageIO;
import java.awt.Graphics2D;
import java.awt.RenderingHints;
import java.awt.image.BufferedImage;
import java.io.DataInputStream;
import java.io.DataOutputStream;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.Iterator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;

/**
 * Builds a picture of everything explored, as region tiles OrbisFox can show in any browser.
 *
 * <p>It only copies map images the server has already drawn for players (the in-game map), so the
 * server does no extra rendering. A low-priority thread looks every 30 seconds, paints new chunk
 * images into 16x16-chunk region tiles and writes them as PNGs at three zoom levels. Old areas
 * nobody has visited since OrbisFox was installed can optionally be filled in slowly, and only
 * while nobody is online ("backfill").</p>
 *
 * Layout under the map folder: {@code <world>/<1|4|16>/<rx>_<rz>.png} and {@code <world>/index.json}.
 */
final class MapCapture {

    static final int REGION = 16;            // chunks per region side
    static final int PX = 32;                // pixels per chunk at level 1 (one per block)
    static final int SIZE = REGION * PX;     // 512
    static final int[] LEVELS = {1, 4, 16};
    private static final long RECAPTURE_MS = 20 * 60 * 1000L;
    private static final int CAPTURE_BUDGET = 800;      // chunk images per cycle
    private static final int FLUSH_BUDGET = 6;          // region writes per cycle
    private static final int BACKFILL_BUDGET = 12;      // old chunks rendered per cycle, empty server only

    private final Path dir;
    private final boolean backfill;
    private final Map<String, Long2LongOpenHashMap> captured = new HashMap<>();
    private final Map<String, BufferedImage> regions = new LinkedHashMap<>(16, 0.75f, true);
    private final java.util.Set<String> dirty = new java.util.LinkedHashSet<>();
    private final Map<String, LongList> savedIndex = new HashMap<>();
    private final Map<String, Long> savedIndexAt = new HashMap<>();
    private ScheduledExecutorService exec;

    MapCapture(Path dir, boolean backfill) {
        this.dir = dir;
        this.backfill = backfill;
    }

    void start() {
        exec = Executors.newSingleThreadScheduledExecutor(r -> {
            Thread t = new Thread(r, "OrbisFoxBridge-map");
            t.setDaemon(true);
            t.setPriority(Thread.MIN_PRIORITY);
            return t;
        });
        exec.scheduleWithFixedDelay(this::cycle, 20, 30, TimeUnit.SECONDS);
    }

    void stop() {
        if (exec == null) return;
        exec.shutdownNow();
        try {
            flush(Integer.MAX_VALUE);
            for (String w : captured.keySet()) saveCaptured(w);
        } catch (Throwable ignored) {
            // best effort on shutdown
        }
    }

    // ------------------------------------------------------------------------------------ cycle
    private void cycle() {
        try {
            Universe u = Universe.get();
            if (u == null) return;
            boolean empty = u.getPlayers().isEmpty();
            long now = System.currentTimeMillis();
            for (World w : u.getWorlds().values()) {
                if (w == null || !w.isAlive()) continue;
                WorldMapManager mgr = w.getWorldMapManager();
                if (mgr == null) continue;
                String name = safe(w.getName());
                Long2LongOpenHashMap cap = captured(name);
                int budget = CAPTURE_BUDGET;
                LongOpenHashSet loaded = new LongOpenHashSet(w.getChunkStore().getChunkIndexes());
                for (LongIterator it = loaded.iterator(); it.hasNext() && budget > 0; ) {
                    long idx = it.nextLong();
                    if (now - cap.getOrDefault(idx, 0L) < RECAPTURE_MS) continue;
                    MapImage img = mgr.getImageIfInMemory(idx);   // already drawn for players: free
                    if (img == null) continue;
                    if (paint(name, idx, img)) {
                        cap.put(idx, now);
                        budget--;
                    }
                }
                if (backfill && empty) backfill(w, mgr, name, cap, now);
            }
            flush(FLUSH_BUDGET);
        } catch (Throwable t) {
            OrbisFoxBridge.LOG.fine("map capture cycle failed: " + t);
        }
    }

    /** Old explored chunks that have no tile yet, a few at a time while nobody is online. */
    private void backfill(World w, WorldMapManager mgr, String name, Long2LongOpenHashMap cap, long now) {
        try {
            LongList saved = savedIndex.get(name);
            if (saved == null || now - savedIndexAt.getOrDefault(name, 0L) > 10 * 60 * 1000L) {
                IChunkLoader loader = w.getChunkStore().getLoader();
                if (loader == null) return;
                saved = loader.getIndexes();
                savedIndex.put(name, saved);
                savedIndexAt.put(name, now);
            }
            int budget = BACKFILL_BUDGET;
            for (int i = 0; i < saved.size() && budget > 0; i++) {
                long idx = saved.getLong(i);
                if (cap.containsKey(idx)) continue;
                MapImage img = mgr.getImageIfInMemory(idx);
                if (img == null) img = mgr.getImageAsync(idx).get(10, TimeUnit.SECONDS);
                if (img != null && paint(name, idx, img)) cap.put(idx, now);
                else cap.put(idx, now - RECAPTURE_MS + 3600_000L);   // retry in an hour
                budget--;
            }
        } catch (Throwable t) {
            OrbisFoxBridge.LOG.fine("map backfill paused: " + t);
        }
    }

    // ------------------------------------------------------------------------------------ painting
    private boolean paint(String world, long idx, MapImage image) throws IOException {
        if (image == null || image.width <= 0 || image.height <= 0 || image.palette == null || image.packedIndices == null) {
            return false;
        }
        int cx = ChunkUtil.xOfChunkIndex(idx), cz = ChunkUtil.zOfChunkIndex(idx);
        int rx = Math.floorDiv(cx, REGION), rz = Math.floorDiv(cz, REGION);
        BufferedImage region = region(world, rx, rz);
        int w = image.width, h = image.height;
        BitFieldArr bits = new BitFieldArr(image.bitsPerIndex, w * h);
        bits.set(image.packedIndices);
        int[] palette = image.palette;
        int ox = (cx - rx * REGION) * PX, oz = (cz - rz * REGION) * PX;
        for (int z = 0; z < PX; z++) {
            int sz = z * h / PX;
            for (int x = 0; x < PX; x++) {
                int i = bits.get(sz * w + x * w / PX);
                int rgba = i >= 0 && i < palette.length ? palette[i] : 0;
                int argb = ((rgba & 0xFF) << 24) | ((rgba >>> 24) << 16) | (((rgba >> 16) & 0xFF) << 8) | ((rgba >> 8) & 0xFF);
                region.setRGB(ox + x, oz + z, argb);
            }
        }
        dirty.add(world + "/" + rx + "_" + rz);
        return true;
    }

    private BufferedImage region(String world, int rx, int rz) throws IOException {
        String key = world + "/" + rx + "_" + rz;
        BufferedImage img = regions.get(key);
        if (img != null) return img;
        Path file = dir.resolve(world).resolve("1").resolve(rx + "_" + rz + ".png");
        BufferedImage onDisk = Files.isRegularFile(file) ? ImageIO.read(file.toFile()) : null;
        img = new BufferedImage(SIZE, SIZE, BufferedImage.TYPE_INT_ARGB);
        if (onDisk != null) {
            Graphics2D g = img.createGraphics();
            g.drawImage(onDisk, 0, 0, SIZE, SIZE, null);
            g.dispose();
        }
        regions.put(key, img);
        // Keep at most a handful of regions in memory (1 MB each).
        while (regions.size() > 8) {
            String eldest = regions.keySet().iterator().next();
            BufferedImage old = regions.remove(eldest);
            if (dirty.contains(eldest)) write(eldest, old);
        }
        return img;
    }

    private void flush(int budget) throws IOException {
        List<String> worlds = new ArrayList<>();
        for (Iterator<String> it = dirty.iterator(); it.hasNext() && budget > 0; budget--) {
            String key = it.next();
            BufferedImage img = regions.get(key);
            it.remove();
            if (img != null) write(key, img);
            String w = key.substring(0, key.indexOf('/'));
            if (!worlds.contains(w)) worlds.add(w);
        }
        for (String w : worlds) {
            writeIndex(w);
            saveCaptured(w);
        }
    }

    private void write(String key, BufferedImage img) throws IOException {
        dirty.remove(key);
        String world = key.substring(0, key.indexOf('/'));
        String name = key.substring(key.indexOf('/') + 1);
        for (int level : LEVELS) {
            BufferedImage out = img;
            if (level > 1) {
                int s = SIZE / level;
                out = new BufferedImage(s, s, BufferedImage.TYPE_INT_ARGB);
                Graphics2D g = out.createGraphics();
                g.setRenderingHint(RenderingHints.KEY_INTERPOLATION, RenderingHints.VALUE_INTERPOLATION_BILINEAR);
                g.drawImage(img, 0, 0, s, s, null);
                g.dispose();
            }
            Path target = dir.resolve(world).resolve(String.valueOf(level)).resolve(name + ".png");
            Files.createDirectories(target.getParent());
            Path tmp = target.resolveSibling(name + ".png.tmp");
            ImageIO.write(out, "png", tmp.toFile());
            Files.move(tmp, target, StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE);
        }
    }

    private void writeIndex(String world) throws IOException {
        Path base = dir.resolve(world).resolve("1");
        List<Object> list = new ArrayList<>();
        if (Files.isDirectory(base)) {
            try (var files = Files.list(base)) {
                for (Path p : (Iterable<Path>) files::iterator) {
                    String n = p.getFileName().toString();
                    if (!n.endsWith(".png")) continue;
                    String[] xz = n.substring(0, n.length() - 4).split("_");
                    try {
                        list.add(new long[]{Long.parseLong(xz[0]), Long.parseLong(xz[1]),
                                Files.getLastModifiedTime(p).toMillis() / 1000});
                    } catch (NumberFormatException ignored) {
                        // not a region file
                    }
                }
            }
        }
        Map<String, Object> index = new LinkedHashMap<>();
        index.put("world", world);
        index.put("region_chunks", REGION);
        index.put("chunk_blocks", 32);
        index.put("levels", new int[]{1, 4, 16});
        index.put("updated", System.currentTimeMillis() / 1000);
        index.put("regions", list);
        Path tmp = dir.resolve(world).resolve("index.json.tmp");
        Files.writeString(tmp, Json.write(index));
        Files.move(tmp, dir.resolve(world).resolve("index.json"), StandardCopyOption.REPLACE_EXISTING,
                StandardCopyOption.ATOMIC_MOVE);
    }

    // ------------------------------------------------------------------------------------ bookkeeping
    private Long2LongOpenHashMap captured(String world) {
        Long2LongOpenHashMap cap = captured.get(world);
        if (cap != null) return cap;
        cap = new Long2LongOpenHashMap();
        Path f = dir.resolve(world).resolve("captured.bin");
        if (Files.isRegularFile(f)) {
            try (DataInputStream in = new DataInputStream(Files.newInputStream(f))) {
                int n = in.readInt();
                for (int i = 0; i < n; i++) cap.put(in.readLong(), in.readLong());
            } catch (IOException ignored) {
                // start over
            }
        }
        captured.put(world, cap);
        return cap;
    }

    private void saveCaptured(String world) throws IOException {
        Long2LongOpenHashMap cap = captured.get(world);
        if (cap == null) return;
        Path f = dir.resolve(world).resolve("captured.bin");
        Files.createDirectories(f.getParent());
        Path tmp = f.resolveSibling("captured.bin.tmp");
        try (DataOutputStream out = new DataOutputStream(new java.io.BufferedOutputStream(Files.newOutputStream(tmp)))) {
            out.writeInt(cap.size());
            for (var e : cap.long2LongEntrySet()) {
                out.writeLong(e.getLongKey());
                out.writeLong(e.getLongValue());
            }
        }
        Files.move(tmp, f, StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE);
    }

    static String safe(String world) {
        return world == null ? "default" : world.replaceAll("[^A-Za-z0-9_.-]", "_");
    }
}
