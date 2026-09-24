"""Messaging: channels, DMs, rooms, and irssi-style conveniences."""

import time

from meshcore import EventType

from . import command


@command("msg", "chat", "/msg <contact|#channel> <text>", "Send a message without switching windows", aliases=("m",))
async def c_msg(app, args):
    first, _, rest = args.partition(" ")
    chan = next((w for w in app.windows if w.kind == "channel" and w.name.lower() == first.lower()), None)
    if args.startswith("#") or chan:
        c, _ = app.split_target(args, fallback=False)
        if c and not args.startswith("#"):  # "Public Works ..." could be a contact or the Public channel
            app.echo(f"Ambiguous: {first} is a channel and {c['adv_name']} is a contact. "
                     f"Use /msg #{first.lstrip('#')} ... or put the contact in \"quotes\".", "error")
            return
        win, text = app.need_channel(first), rest
        if not win:
            return
    else:
        c, text = app.split_target(args)
        if not c:
            app.echo(f"/msg: {app.resolve_error or 'usage: /msg <contact> <text>'}", "error")
            return
        win = app.query_window(c)
    if not text.strip():
        app.echo(f"Nothing to send. /query {win.name} opens the window.", "error")
        return
    await app.say(win, text)


@command("query", "chat", "/query <contact>", "Open a DM window with a contact", aliases=("dm",))
async def c_query(app, args):
    c = app.need_contact(args)
    if c:
        app.switch(app.windows.index(app.query_window(c)))


@command("join", "chat", "/join <#hashtag> | <name> <32-hex-key>", "Add a channel to a free slot on the radio")
async def c_join(app, args):
    parts = args.split()
    if not parts:
        app.echo("Usage: /join #hashtag   or   /join <name> <key-hex>", "error")
        return
    name = parts[0]
    existing = next((w for w in app.windows if w.kind == "channel" and w.name.lower() == name.lower()), None)
    if existing:
        app.switch(app.windows.index(existing))
        return
    if name.startswith("#"):
        if len(parts) > 1:
            app.echo(f"{name} is a hashtag channel: its key comes from the name, so anyone can join it and the key "
                     f"you gave would be ignored. For a private channel use a name without #.", "error")
            return
        secret = None
    elif len(parts) == 2 and len(parts[1]) == 32 and all(ch in "0123456789abcdefABCDEF" for ch in parts[1]):
        secret = bytes.fromhex(parts[1])
    else:
        app.echo("Private channels need their 16-byte key as 32 hex chars. Hashtag channels (#name) derive it.", "error")
        return
    if len(name.encode()) > 31:
        app.echo("Channel names are limited to 31 bytes on the radio.", "error")
        return
    free = next((i for i in range(1, app.device_info.get("max_channels", 8)) if i not in app.channels), None)
    if free is None:
        app.echo("No free channel slots on the radio.", "error")
        return
    await app.cmd(app.mc.commands.set_channel(free, name, secret))
    await app.load_channels()
    win = next((w for w in app.windows if w.kind == "channel" and w.channel_idx == free), None)
    if win is None:
        app.echo(f"The radio didn't keep {name} in slot {free}.", "error")
        return
    app.switch(app.windows.index(win))
    app.status(f"Joined {name} (slot {free})", "join", win=win)


@command("part", "chat", "/part [#channel]", "Remove a channel from the radio", aliases=("leave",))
async def c_part(app, args):
    win = app.need_channel(args)
    if not win:
        return
    what = "slot 0, the public channel" if win.channel_idx == 0 else (
        "hashtag: rejoin any time with /join" if win.name.startswith("#") else
        "PRIVATE: its key is deleted from the radio; /key shows it if you need to save it")
    if not app.confirm(f"part {win.name} ({what})"):
        return
    await app.cmd(app.mc.commands.set_channel(win.channel_idx, "", bytes(16)))
    app.channels.pop(win.channel_idx, None)
    app.close_window(win)
    app.status(f"Left {win.name}")


@command("channels", "chat", "/channels", "List channels configured on the radio", aliases=("list",))
async def c_channels(app, args):
    for idx, ch in sorted(app.channels.items()):
        app.echo(f"slot {idx:>2}  {ch['channel_name']:<24} hash {ch['channel_hash']}")


@command("key", "chat", "/key [#channel]", "Show a channel's secret key (to share it)")
async def c_key(app, args):
    win = app.need_channel(args)
    if win:
        secret = app.channels[win.channel_idx]["channel_secret"]
        app.echo(f"{win.name} key: {secret.hex()}   (others join with /join {win.name} {secret.hex()})")


@command("lastlog", "chat", "/lastlog [-all] <text>", "Search scrollback (current window, or all with -all)", aliases=("grep",))
async def c_lastlog(app, args):
    everywhere = args == "-all" or args.startswith("-all ")
    needle = args[4:].strip() if everywhere else args
    if not needle:
        app.echo("Usage: /lastlog [-all] <text>", "error")
        return
    wins = [w for w in app.windows if w.kind in ("channel", "query")] if everywhere else [app.win]
    hits = []
    for w in wins:
        recs = list(w.recs)
        if everywhere and app.store:  # older history that's no longer in memory
            seen = {r.get("id") for r in recs}
            recs = [r for r in app.store.load(w.key, limit=5000) if r.get("id") not in seen] + recs
        for r in recs:
            if r.get("k") in ("msg", "reply", "notice") and not r.get("echo") and needle.lower() in (r.get("nick", "") + " " + r.get("text", "")).lower():
                hits.append((w, r))
    hits.sort(key=lambda wr: wr[1].get("t", 0))
    app.echo(f"lastlog: {len(hits)} match(es) for {needle!r}" + (" in all windows" if everywhere else ""))
    for w, r in hits[-200:]:
        when = time.strftime("%m-%d %H:%M", time.localtime(r.get("t", 0)))
        who = f"<{r['nick']}> " if r.get("nick") else ""
        app.echo(f"{when} {w.name if everywhere else ''} {who}{r.get('text', '')}".replace("  ", " "), "dim")


@command("away", "chat", "/away [message]", "Mark yourself away; DMs get one auto-reply each. No message = back")
async def c_away(app, args):
    if not args and app.away is not None:
        app.away = None
        app.away_replied.clear()
        app.echo("You are no longer marked as away.", "ok")
    else:
        app.away = args or app.cfg.get("chat.away_message") or ""
        app.away_replied.clear()
        app.echo(f"You are now away{': ' + app.away if app.away else ''}. DMs from people get one auto-reply.", "ok")
    app.refresh_statusbar()


@command("back", "chat", "/back", "Clear away status")
async def c_back(app, args):
    app.away = None
    app.away_replied.clear()
    app.echo("Welcome back.", "ok")
    app.refresh_statusbar()


@command("ignore", "chat", "/ignore [nick-pattern]", "Hide messages from a nick (globs ok); no argument lists them")
async def c_ignore(app, args):
    ign = list(app.cfg.get("chat.ignores", []))
    if not args:
        app.echo(f"Ignoring: {', '.join(ign) or 'nobody'} ({app.ignored} message(s) hidden this session)")
        return
    if args not in ign:
        ign.append(args)
        app.cfg["chat"]["ignores"] = ign
        app.cfg.save()
    app.echo(f"Ignoring {args}", "ok")


@command("unignore", "chat", "/unignore <nick-pattern>", "Stop ignoring a nick")
async def c_unignore(app, args):
    ign = [i for i in app.cfg.get("chat.ignores", []) if i != args]
    app.cfg["chat"]["ignores"] = ign
    app.cfg.save()
    app.echo(f"No longer ignoring {args}", "ok")


@command("hilight", "chat", "/hilight [-del] [word]", "Words that highlight channel lines; no argument lists them", aliases=("highlight",))
async def c_hilight(app, args):
    words = list(app.cfg.get("chat.highlights", []))
    if not args:
        app.echo(f"Highlights: your name, {', '.join(words) or '(no extra words)'}")
        return
    if args.startswith("-del "):
        words = [w for w in words if w.lower() != args[5:].strip().lower()]
    elif args not in words:
        words.append(args)
    app.cfg["chat"]["highlights"] = words
    app.cfg.save()
    app.echo(f"Highlights: {', '.join(words) or '(none)'}", "ok")


@command("room", "chat", "/room <room> [password] [-save]", "Log into a room server and open its window; -save auto-logs in on connect")
async def c_room(app, args):
    save = args.endswith(" -save")
    c, pwd = app.split_target(args.removesuffix(" -save"), fallback=False)
    if not c or c["type"] != 3:
        app.echo(f"/room: {app.resolve_error + '. ' if app.resolve_error else ''}"
                 "usage: /room <room server> [password] [-save] (see /contacts for rooms)", "error")
        return
    win = app.query_window(c)
    app.switch(app.windows.index(win))
    app.status(f"Logging into room {c['adv_name']}… it will send posts since your last visit.", win=win)
    ev = await app.mesh_request(app.mc.commands.send_login_sync, c, pwd)
    if ev is not None and ev.type == EventType.LOGIN_SUCCESS:
        app.status(f"Logged into {c['adv_name']}. Anything you type here is posted to the room.", "ok", win=win)
        if save:
            app.cfg["rooms"][c["public_key"]] = pwd  # by key, so a look-alike name never gets the password
            app.cfg.save()
            app.status("Password saved to config.toml for auto-login.", "dim", win=win)
    else:
        app.status("Login failed or timed out.", "error", win=win)
