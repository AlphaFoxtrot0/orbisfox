package com.alphafoxtrot.orbisfox.bridge;

import com.hypixel.hytale.server.core.universe.world.World;

import javax.imageio.ImageIO;
import java.awt.image.BufferedImage;
import java.io.ByteArrayOutputStream;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.lang.reflect.Modifier;
import java.util.ArrayList;
import java.util.Collection;
import java.util.HashSet;
import java.util.Iterator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;

/**
 * Which chunks a world has loaded, and the server's own map image for one of them.
 *
 * <p>The server API has a loaded-chunk count but no documented list, so the list is found by
 * looking at the chunk store's methods at run time (read on the world thread). If that fails on a
 * future version, the plugin falls back to the chunks inside each player's view and says the list
 * is not exact. Map images are only requested for chunks that are loaded, so the map never makes
 * the server load or generate terrain.</p>
 */
final class Chunks {

    private static final int CHUNK = 32;
    private static final int FALLBACK_RADIUS = 12;

    record Result(List<int[]> chunks, boolean exact, int chunkSize) { }

    private Method listMethod;           // ChunkStore method that returns the loaded chunk indexes
    private boolean searched;
    private Method decodeX, decodeZ;     // ChunkUtil long -> int
    private final Map<String, Set<Long>> lastLoaded = new LinkedHashMap<>();
    private final Map<String, byte[]> tiles = new LinkedHashMap<>(256, 0.75f, true) {
        @Override
        protected boolean removeEldestEntry(Map.Entry<String, byte[]> e) {
            return size() > 3000;
        }
    };

    Result loaded(World world, List<Tracker.Player> players) {
        try {
            Set<Long> idx = onWorldThread(world, () -> readIndexes(world));
            if (idx != null && !idx.isEmpty()) {
                List<int[]> out = new ArrayList<>(idx.size());
                for (long i : idx) out.add(new int[]{decode(i, true), decode(i, false), 0});
                synchronized (lastLoaded) {
                    lastLoaded.put(world.getName(), idx);
                }
                return new Result(out, true, CHUNK);
            }
        } catch (Throwable t) {
            OrbisFoxBridge.LOG.fine("loaded chunk list unavailable: " + t);
        }
        // Fallback: what players can see.
        Set<Long> seen = new HashSet<>();
        List<int[]> out = new ArrayList<>();
        int r = FALLBACK_RADIUS;
        for (Tracker.Player p : players) {
            int pcx = (int) Math.floor(p.x / CHUNK), pcz = (int) Math.floor(p.z / CHUNK);
            for (int dx = -r; dx <= r; dx++) {
                for (int dz = -r; dz <= r; dz++) {
                    if (dx * dx + dz * dz > r * r) continue;
                    long key = (((long) (pcx + dx)) << 32) ^ ((pcz + dz) & 0xffffffffL);
                    if (seen.add(key)) out.add(new int[]{pcx + dx, pcz + dz, 0});
                }
            }
        }
        return new Result(out, false, CHUNK);
    }

    private Set<Long> readIndexes(World world) throws Exception {
        Object store = world.getChunkStore();
        if (!searched) {
            searched = true;
            for (Method m : store.getClass().getMethods()) {
                if (m.getParameterCount() != 0 || Modifier.isStatic(m.getModifiers())) continue;
                String n = m.getName().toLowerCase();
                if (n.contains("count") || n.contains("size")) continue;
                boolean looks = (n.contains("loaded") && n.contains("chunk")) || n.equals("getchunkindexes")
                        || n.equals("getloadedindexes") || n.equals("getchunks");
                if (looks && convertible(m.getReturnType())) {
                    listMethod = m;
                    break;
                }
            }
            if (listMethod != null) OrbisFoxBridge.LOG.info("Loaded-chunk list via ChunkStore." + listMethod.getName());
            else OrbisFoxBridge.LOG.info("No loaded-chunk list in this server version; the map shows players' view areas.");
        }
        if (listMethod == null) return null;
        return toLongs(listMethod.invoke(store));
    }

    private static boolean convertible(Class<?> c) {
        return Collection.class.isAssignableFrom(c) || Map.class.isAssignableFrom(c) || c == long[].class
                || Iterable.class.isAssignableFrom(c);
    }

    private static Set<Long> toLongs(Object o) {
        Set<Long> out = new HashSet<>();
        if (o == null) return out;
        if (o instanceof long[] arr) {
            for (long v : arr) out.add(v);
            return out;
        }
        if (o instanceof Map<?, ?> m) o = m.keySet();
        if (o instanceof Iterable<?> it) {
            Iterator<?> iter = it.iterator();
            while (iter.hasNext()) {
                Object v = iter.next();
                if (v instanceof Long l) out.add(l);
                else if (v instanceof Number num) out.add(num.longValue());
            }
        }
        return out;
    }

    private int decode(long index, boolean x) {
        if (decodeX == null && decodeZ == null) {
            try {
                Class<?> cu = Class.forName("com.hypixel.hytale.math.util.ChunkUtil");
                for (Method m : cu.getMethods()) {
                    if (!Modifier.isStatic(m.getModifiers()) || m.getParameterCount() != 1
                            || m.getParameterTypes()[0] != long.class || m.getReturnType() != int.class) continue;
                    String n = m.getName().toLowerCase();
                    if (n.contains("x") && !n.contains("z") && decodeX == null) decodeX = m;
                    if (n.contains("z") && !n.contains("x") && decodeZ == null) decodeZ = m;
                }
            } catch (Throwable ignored) {
                // use the common packing below
            }
            if (decodeX == null) decodeX = NONE;
            if (decodeZ == null) decodeZ = NONE;
        }
        Method m = x ? decodeX : decodeZ;
        if (m != NONE) {
            try {
                return (int) m.invoke(null, index);
            } catch (Throwable ignored) {
                // fall through
            }
        }
        return x ? (int) (index >> 32) : (int) index;
    }

    private static final Method NONE;
    static {
        Method m = null;
        try {
            m = Object.class.getMethod("hashCode");
        } catch (NoSuchMethodException ignored) {
            // impossible
        }
        NONE = m;
    }

    // ------------------------------------------------------------------------------------ map images
    byte[] tile(World world, int cx, int cz) {
        String key = world.getName() + ":" + cx + ":" + cz;
        synchronized (tiles) {
            byte[] cached = tiles.get(key);
            if (cached != null) return cached;
        }
        Set<Long> loaded;
        synchronized (lastLoaded) {
            loaded = lastLoaded.get(world.getName());
        }
        if (loaded == null) return null;     // only exact lists allow images
        boolean isLoaded = false;
        for (long i : loaded) {
            if (decode(i, true) == cx && decode(i, false) == cz) {
                isLoaded = true;
                break;
            }
        }
        if (!isLoaded) return null;
        try {
            Object mgr = world.getClass().getMethod("getWorldMapManager").invoke(world);
            Method get = mgr.getClass().getMethod("getImageAsync", int.class, int.class);
            Object fut = get.invoke(mgr, cx, cz);
            Object image = fut instanceof Future<?> f ? f.get(4, TimeUnit.SECONDS) : fut;
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

    private static byte[] encode(Object image) throws Exception {
        if (image == null) return null;
        Class<?> c = image.getClass();
        int w = intField(c, image, "width"), h = intField(c, image, "height");
        Field df = c.getField("data");
        int[] data = (int[]) df.get(image);
        if (w <= 0 || h <= 0 || data == null || data.length < w * h) return null;
        BufferedImage img = new BufferedImage(w, h, BufferedImage.TYPE_INT_ARGB);
        for (int y = 0; y < h; y++) {
            for (int x = 0; x < w; x++) {
                int rgba = data[y * w + x];
                int argb = ((rgba & 0xFF) << 24) | ((rgba >>> 24) << 16) | (((rgba >> 16) & 0xFF) << 8) | ((rgba >> 8) & 0xFF);
                img.setRGB(x, y, argb);
            }
        }
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        ImageIO.write(img, "png", out);
        return out.toByteArray();
    }

    private static int intField(Class<?> c, Object o, String name) throws Exception {
        return ((Number) c.getField(name).get(o)).intValue();
    }

    private static <T> T onWorldThread(World world, java.util.concurrent.Callable<T> task) throws Exception {
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
