package com.alphafoxtrot.orbisfox.bridge;

import com.hypixel.hytale.event.EventPriority;
import com.hypixel.hytale.server.core.event.events.player.PlayerChatEvent;
import com.hypixel.hytale.server.core.event.events.player.PlayerConnectEvent;
import com.hypixel.hytale.server.core.event.events.player.PlayerDisconnectEvent;
import com.hypixel.hytale.server.core.plugin.JavaPlugin;
import com.hypixel.hytale.server.core.plugin.JavaPluginInit;

import java.util.logging.Logger;

/**
 * OrbisFox Bridge: reports what the OrbisFox panel cannot read from the console.
 *
 * <p>Players with position and ping, per-world tick rate, loaded chunks and how quickly each world
 * thread answers, deaths and kills, distance walked, chat, and map images of loaded chunks. It only
 * listens on 127.0.0.1, and only when the server was started by OrbisFox, which passes the port and
 * a token through the environment (ORBISFOX_BRIDGE_PORT, ORBISFOX_BRIDGE_TOKEN). On a server started
 * any other way the plugin stays idle.</p>
 */
public class OrbisFoxBridge extends JavaPlugin {

    public static final String VERSION = "1.1.0";
    static final Logger LOG = Logger.getLogger("OrbisFoxBridge");

    private final Tracker tracker = new Tracker();
    private BridgeServer http;
    private MapCapture map;

    public OrbisFoxBridge(JavaPluginInit init) {
        super(init);
    }

    @Override
    protected void setup() {
        try {
            getEventRegistry().register(PlayerConnectEvent.class,
                    e -> tracker.onJoin(e.getPlayerRef().getUuid(), e.getPlayerRef().getUsername()));
            getEventRegistry().register(PlayerDisconnectEvent.class,
                    e -> tracker.onLeave(e.getPlayerRef().getUuid()));
        } catch (Throwable t) {
            LOG.warning("Join/leave events unavailable: " + t);
        }
        try {
            getEventRegistry().registerGlobal(EventPriority.LAST, PlayerChatEvent.class, e -> {
                if (e.isCancelled()) return;
                var sender = e.getSender();
                if (sender != null) tracker.onChat(sender.getUuid(), sender.getUsername(), e.getContent());
            });
        } catch (Throwable t) {
            LOG.warning("Chat events unavailable: " + t);
        }
        try {
            getEntityStoreRegistry().registerSystem(new DeathWatcher(tracker));
        } catch (Throwable t) {
            LOG.warning("Death tracking unavailable: " + t);
        }
        try {
            String name = System.getenv().getOrDefault("ORBISFOX_VETO_COMMAND", "veto").trim().toLowerCase();
            getCommandRegistry().registerCommand(new VetoCommand(name.matches("[a-z][a-z0-9_-]{1,23}") ? name : "veto", tracker));
        } catch (Throwable t) {
            LOG.warning("Could not register /veto: " + t);
        }
    }

    @Override
    protected void start() {
        String port = System.getenv("ORBISFOX_BRIDGE_PORT");
        String token = System.getenv("ORBISFOX_BRIDGE_TOKEN");
        if (port == null || token == null || token.isBlank()) {
            LOG.info("OrbisFox Bridge " + VERSION + ": not started by OrbisFox, staying idle.");
            return;
        }
        try {
            tracker.start();
            http = new BridgeServer(Integer.parseInt(port.trim()), token, tracker);
            http.start();
            LOG.info("OrbisFox Bridge " + VERSION + " listening on 127.0.0.1:" + port);
            String mapDir = System.getenv("ORBISFOX_MAP_DIR");
            if (mapDir != null && !mapDir.isBlank()) {
                map = new MapCapture(java.nio.file.Path.of(mapDir), "1".equals(System.getenv("ORBISFOX_MAP_BACKFILL")));
                map.start();
            }
        } catch (Exception e) {
            LOG.severe("OrbisFox Bridge could not start: " + e);
        }
    }

    @Override
    protected void shutdown() {
        if (map != null) map.stop();
        if (http != null) http.stop();
        tracker.stop();
    }
}
