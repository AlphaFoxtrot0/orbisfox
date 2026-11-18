package com.alphafoxtrot.orbisfox.bridge;

import com.hypixel.hytale.component.CommandBuffer;
import com.hypixel.hytale.component.Ref;
import com.hypixel.hytale.component.Store;
import com.hypixel.hytale.component.query.Query;
import com.hypixel.hytale.server.core.Message;
import com.hypixel.hytale.server.core.modules.entity.component.DisplayNameComponent;
import com.hypixel.hytale.server.core.modules.entity.component.TransformComponent;
import com.hypixel.hytale.server.core.modules.entity.damage.Damage;
import com.hypixel.hytale.server.core.modules.entity.damage.DamageCause;
import com.hypixel.hytale.server.core.modules.entity.damage.DeathComponent;
import com.hypixel.hytale.server.core.modules.entity.damage.DeathSystems;
import com.hypixel.hytale.server.core.universe.PlayerRef;
import com.hypixel.hytale.server.core.universe.world.World;
import com.hypixel.hytale.server.core.universe.world.storage.EntityStore;

/**
 * Told on the world thread whenever an entity with a position dies. A dead player counts a death
 * (and a kill for the player who did it); a dead creature counts a kill for the player who did it.
 */
final class DeathWatcher extends DeathSystems.OnDeathSystem {

    private final Tracker tracker;

    DeathWatcher(Tracker tracker) {
        this.tracker = tracker;
    }

    @Override
    public Query<EntityStore> getQuery() {
        return TransformComponent.getComponentType();
    }

    @Override
    public void onComponentAdded(Ref<EntityStore> ref, DeathComponent death, Store<EntityStore> store,
                                 CommandBuffer<EntityStore> commandBuffer) {
        try {
            PlayerRef victim = store.getComponent(ref, PlayerRef.getComponentType());
            PlayerRef killer = null;
            String killerName = null;
            Damage damage = death.getDeathInfo();
            if (damage != null && damage.getSource() instanceof Damage.EntitySource src) {
                Ref<EntityStore> kref = src.getRef();
                if (kref != null && kref.isValid() && kref.getStore() == store) {
                    killer = store.getComponent(kref, PlayerRef.getComponentType());
                    if (killer != null) {
                        killerName = killer.getUsername();
                    } else {
                        DisplayNameComponent dn = store.getComponent(kref, DisplayNameComponent.getComponentType());
                        killerName = dn != null ? plain(dn.getDisplayName()) : null;
                    }
                }
            }
            if (victim != null) {
                World world = store.getExternalData().getWorld();
                TransformComponent t = store.getComponent(ref, TransformComponent.getComponentType());
                var pos = t != null ? t.getPosition() : null;
                tracker.onPlayerDeath(victim.getUuid(), victim.getUsername(), cause(death.getDeathCause()), killerName,
                        world != null ? world.getName() : null, pos != null ? pos.x : 0, pos != null ? pos.z : 0);
                if (killer != null && !killer.getUuid().equals(victim.getUuid())) {
                    tracker.onKill(killer.getUuid(), killer.getUsername(), victim.getUsername(), true);
                }
            } else if (killer != null) {
                DisplayNameComponent dn = store.getComponent(ref, DisplayNameComponent.getComponentType());
                tracker.onKill(killer.getUuid(), killer.getUsername(), dn != null ? plain(dn.getDisplayName()) : null, false);
            }
        } catch (Throwable t) {
            OrbisFoxBridge.LOG.fine("death not recorded: " + t);
        }
    }

    private static String cause(DamageCause c) {
        String id = c != null ? c.getId() : null;
        return id != null && !id.isBlank() ? id : "unknown";
    }

    private static String plain(Message m) {
        if (m == null) return null;
        String raw = m.getRawText();
        if (raw != null && !raw.isBlank()) return raw;
        String key = m.getMessageId();
        return key != null && !key.isBlank() ? key : null;
    }
}
