package com.alphafoxtrot.orbisfox.bridge;

import com.hypixel.hytale.math.util.ChunkUtil;
import com.hypixel.hytale.protocol.packets.worldmap.MapImage;
import com.hypixel.hytale.server.core.universe.world.World;
import com.hypixel.hytale.server.core.universe.world.chunk.palette.BitFieldArr;
import com.hypixel.hytale.server.core.universe.world.storage.ChunkStore;
import com.hypixel.hytale.server.core.universe.world.worldmap.WorldMapManager;
import it.unimi.dsi.fastutil.longs.LongIterator;
import it.unimi.dsi.fastutil.longs.LongOpenHashSet;
import it.unimi.dsi.fastutil.longs.LongSet;

import javax.imageio.ImageIO;
import java.awt.image.BufferedImage;
import java.io.ByteArrayOutputStream;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.Callable;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.TimeUnit;

/**
 * Which chunks a world has loaded (with those still loading or generating marked), and the
 * server's own world-map image for a loaded chunk.
 *
 * <p>Map images come from the world map's in-memory cache when possible; otherwise they are
 * rendered only for chunks that are already loaded, so the map never makes the server load or
 * generate terrain. If a future server version changes these APIs, the plugin falls back to the
 * chunks inside each player's view and reports the list as not exact.</p>
 */
final class Chunks {

    private static final int CHUNK = 32;
    private static final int FALLBACK_RADIUS = 12;

    record Result(List<int[]> chunks, boolean exact, int chunkSize) { }

    private final Map<String, Set<Long>> lastLoaded = new LinkedHashMap<>();
    private final Map<String, byte[]> tiles = new LinkedHashMap<>(256, 0.75f, true) {
        @Override
        protected boolean removeEldestEntry(Map.Entry<String, byte[]> e) {
            return size() > 3000;
        }
    };

    Result loaded(World world, List<Tracker.Player> players) {
        try {
            List<long[]> found = onWorldThread(world, () -> read(world));
            List<int[]> out = new ArrayList<>(found.size());
            Set<Long> ready = new HashSet<>();
            for (long[] c : found) {
                out.add(new int[]{ChunkUtil.xOfChunkIndex(c[0]), ChunkUtil.zOfChunkIndex(c[0]), (int) c[1]});
                if (c[1] == 0) ready.add(c[0]);
            }
            synchronized (lastLoaded) {
                lastLoaded.put(world.getName(), ready);
            }
            return new Result(out, true, CHUNK);
        } catch (Throwable t) {
            OrbisFoxBridge.LOG.fine("loaded chunk list unavailable: " + t);
        }
        return approximate(players);
    }

    /** On the world thread: every chunk the store tracks; state 0 = loaded, 1 = still loading/generating. */
    private static List<long[]> read(World world) {
        ChunkStore store = world.getChunkStore();
        LongSet indexes = new LongOpenHashSet(store.getChunkIndexes());
        List<long[]> out = new ArrayList<>(indexes.size());
        for (LongIterator it = indexes.iterator(); it.hasNext(); ) {
            long idx = it.nextLong();
            boolean ready;
            try {
                var ref = store.getChunkReference(idx);
                ready = ref != null && ref.isValid();
            } catch (RuntimeException e) {
                ready = true;
            }
            out.add(new long[]{idx, ready ? 0 : 1});
        }
        return out;
    }

    private static Result approximate(List<Tracker.Player> players) {
        Set<Long> seen = new HashSet<>();
        List<int[]> out = new ArrayList<>();
        int r = FALLBACK_RADIUS;
        for (Tracker.Player p : players) {
            int pcx = (int) Math.floor(p.x / CHUNK), pcz = (int) Math.floor(p.z / CHUNK);
            for (int dx = -r; dx <= r; dx++) {
                for (int dz = -r; dz <= r; dz++) {
                    if (dx * dx + dz * dz > r * r) continue;
                    if (seen.add(ChunkUtil.indexChunk(pcx + dx, pcz + dz))) out.add(new int[]{pcx + dx, pcz + dz, 0});
                }
            }
        }
        return new Result(out, false, CHUNK);
    }

    // ------------------------------------------------------------------------------------ map images
    byte[] tile(World world, int cx, int cz) {
        String key = world.getName() + ":" + cx + ":" + cz;
        synchronized (tiles) {
            byte[] cached = tiles.get(key);
            if (cached != null) return cached;
        }
        try {
            WorldMapManager mgr = world.getWorldMapManager();
            if (mgr == null) return null;
            MapImage image = mgr.getImageIfInMemory(cx, cz);
            if (image == null) {
                Set<Long> loaded;
                synchronized (lastLoaded) {
                    loaded = lastLoaded.get(world.getName());
                }
                if (loaded == null || !loaded.contains(ChunkUtil.indexChunk(cx, cz))) return null;
                image = mgr.getImageAsync(cx, cz).get(4, TimeUnit.SECONDS);
            }
            byte[] png = encode(image);
            if (png != null) {
                synchronized (tiles) {
                    tiles.put(key, png);
                }
            }
            return png;
        } catch (Throwable t) {
            OrbisFoxBridge.LOG.fine("map image unavailable: " + t);
            return null;
        }
    }

    /** MapImage is palette-encoded: each pixel is an index into `palette` (RGBA colours). */
    private static byte[] encode(MapImage image) throws Exception {
        if (image == null || image.width <= 0 || image.height <= 0 || image.palette == null || image.packedIndices == null) {
            return null;
        }
        int w = image.width, h = image.height;
        BitFieldArr bits = new BitFieldArr(image.bitsPerIndex, w * h);
        bits.set(image.packedIndices);
        int[] palette = image.palette;
        BufferedImage img = new BufferedImage(w, h, BufferedImage.TYPE_INT_ARGB);
        for (int y = 0; y < h; y++) {
            for (int x = 0; x < w; x++) {
                int i = bits.get(y * w + x);
                int rgba = i >= 0 && i < palette.length ? palette[i] : 0;
                int argb = ((rgba & 0xFF) << 24) | ((rgba >>> 24) << 16) | (((rgba >> 16) & 0xFF) << 8) | ((rgba >> 8) & 0xFF);
                img.setRGB(x, y, argb);
            }
        }
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        ImageIO.write(img, "png", out);
        return out.toByteArray();
    }

    private static <T> T onWorldThread(World world, Callable<T> task) throws Exception {
        if (world.isInThread()) return task.call();
        CompletableFuture<T> f = new CompletableFuture<>();
        world.execute(() -> {
            try {
                f.complete(task.call());
            } catch (Throwable t) {
                f.completeExceptionally(t);
            }
        });
        return f.get(3, TimeUnit.SECONDS);
    }
}
