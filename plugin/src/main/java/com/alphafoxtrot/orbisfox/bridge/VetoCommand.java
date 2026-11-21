package com.alphafoxtrot.orbisfox.bridge;

import com.hypixel.hytale.server.core.Message;
import com.hypixel.hytale.server.core.command.system.AbstractCommand;
import com.hypixel.hytale.server.core.command.system.CommandContext;
import com.hypixel.hytale.server.core.command.system.CommandSender;

import java.util.concurrent.CompletableFuture;

/**
 * /veto: lets an operator cancel the automatic restart or update OrbisFox is counting down to.
 * Only players with the "orbisfox.veto" permission may use it (operators have every permission).
 */
final class VetoCommand extends AbstractCommand {

    static final String PERMISSION = "orbisfox.veto";
    private final Tracker tracker;

    VetoCommand(String name, Tracker tracker) {
        super(name, "Cancel OrbisFox's automatic restart or update");
        this.tracker = tracker;
        requirePermission(PERMISSION);
    }

    @Override
    protected CompletableFuture<Void> execute(CommandContext ctx) {
        CommandSender sender = ctx.sender();
        tracker.onVeto(sender.getUuid(), sender.getUsername());
        ctx.sendMessage(Message.raw("[OrbisFox] Cancel request sent.").color("#4DC4D8"));
        return CompletableFuture.completedFuture(null);
    }
}
