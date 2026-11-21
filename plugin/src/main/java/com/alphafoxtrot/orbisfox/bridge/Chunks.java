package com.alphafoxtrot.orbisfox.bridge;

import com.hypixel.hytale.math.util.ChunkUtil;
import com.hypixel.hytale.server.core.universe.world.World;
import it.unimi.dsi.fastutil.longs.LongIterator;
import it.unimi.dsi.fastutil.longs.LongOpenHashSet;

import java.util.ArrayList;
import java.util.List;

/**
 * Which chunks a world has in memory right now, for OrbisFox's "loaded chunks" overlay.
 * Read straight from the chunk store's concurrent index (no world-thread work), and only when
 * someone has the overlay open.
 */
final class Chunks {

    record Result(List<int[]> chunks, boolean exact) { }

    Result loaded(World world) {
        try {
            LongOpenHashSet indexes = new LongOpenHashSet(world.getChunkStore().getChunkIndexes());
            List<int[]> out = new ArrayList<>(indexes.size());
            for (LongIterator it = indexes.iterator(); it.hasNext(); ) {
                long idx = it.nextLong();
                out.add(new int[]{ChunkUtil.xOfChunkIndex(idx), ChunkUtil.zOfChunkIndex(idx)});
            }
            return new Result(out, true);
        } catch (Throwable t) {
            OrbisFoxBridge.LOG.fine("loaded chunk list unavailable: " + t);
            return new Result(List.of(), false);
        }
    }
}
