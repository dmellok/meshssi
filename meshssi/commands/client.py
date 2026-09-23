"""Client-side commands: windows, settings, themes, aliases, plugins."""

import asyncio

from rich.markup import escape

from ..themes import THEMES
from . import ALIASES, CATEGORIES, COMMANDS, command


@command("help", "client", "/help [command|category]", "List commands, or show help for one")
async def c_help(app, args):
    arg = args.lstrip("/").lower()
    if arg and arg not in CATEGORIES:
        name = ALIASES.get(arg, arg)
        if name in COMMANDS:
            _, _, usage, text = COMMANDS[name]
            als = [a for a, n in ALIASES.items() if n == name]
            app.echo(f"[bold]{escape(usage)}[/]  {escape(text)}" + (f"  (aliases: {', '.join('/' + a for a in als)})" if als else ""),
                     markup=True)
        elif arg in app.cfg["aliases"]:
            app.echo(f"/{arg} is your alias for {app.cfg['aliases'][arg]}")
        else:
            app.echo(f"No such command: {args}", "error")
        return
    cats: dict[str, list[str]] = {}
    for n, (_, cat, usage, text) in sorted(COMMANDS.items()):
        cats.setdefault(cat, []).append(f"  [bold]{escape(f'{usage:<44}')}[/] [{app.st['dim']}]{escape(text)}[/]")
    for cat in CATEGORIES:
        if arg and cat != arg or not cats.get(cat):
            continue
        app.echo(f"[bold underline]{cat}[/]", markup=True)
        for row in cats[cat]:
            app.echo(row, markup=True)
    if not arg:
        app.echo("Keys: alt+1..0 / esc N jump · ctrl+n/p cycle · ctrl+a next active · F2 nicklist · PgUp/PgDn scroll · "
                 "tab complete · ↑/↓ history.  /help <category> shows one group.")


@command("window", "client", "/window <n>|close|list|move <n>", "Switch, close, list or reorder windows", aliases=("win", "w"))
async def c_window(app, args):
    if args.isdigit():
        app.switch(int(args) - 1)
    elif args in ("close", "c"):
        await c_wc(app, "")
    elif args.startswith("move ") and args[5:].strip().isdigit():
        to = max(1, min(len(app.windows), int(args[5:]))) - 1
        if app.current == 0 or to == 0:
            app.echo("The status window stays at 1.", "error")
            return
        w = app.windows.pop(app.current)
        app.windows.insert(to, w)
        app.current = to
        app.refresh_chrome()
    else:
        for i, w in enumerate(app.windows):
            app.echo(f"{i + 1:>2}: {w.name:<24} {w.view or w.kind}")


@command("wc", "client", "/wc", "Close the current window (use /part to leave a channel)", aliases=("close",))
async def c_wc(app, args):
    if app.win.kind == "channel":
        app.echo("Use /part to leave (and delete) a channel.", "error")
    else:
        app.close_window(app.win)


@command("split", "client", "/split [n|off]", "Show another window's scrollback above this one")
async def c_split(app, args):
    if not args or args == "off":
        app.split_win = None
    elif args.isdigit() and 0 < int(args) <= len(app.windows):
        app.split_win = app.windows[int(args) - 1]
    else:
        app.echo("Usage: /split <window number> | /split off", "error")
        return
    app.redraw()


@command("clear", "client", "/clear", "Clear the current window's scrollback on screen")
async def c_clear(app, args):
    app.win.recs.clear()
    app.redraw()


@command("set", "client", "/set [section.key [value]] | /set <device-setting> <value>",
         "Show or change settings (config.toml); device settings: manualadd, multiacks, locpolicy, pathhash, autoadd")
async def c_set(app, args):
    key, _, val = args.partition(" ")
    if key in DEVICE_SETTINGS:
        from .device import set_device

        await set_device(app, key, val)
        return
    if not key:
        for section, values in app.cfg.data.items():
            app.echo(f"[{section}]", "dim")
            for k, v in values.items():
                shown = "***" if section == "rooms" else v
                app.echo(f"  {section}.{k} = {shown!r}")
        app.echo(f"Device settings (on the radio): {', '.join(DEVICE_SETTINGS)} — see /set <name>. File: {app.cfg.path}")
        return
    if not val:
        app.echo(f"{key} = {app.cfg.get(key)!r}")
        return
    if val == "-default":
        app.cfg.unset(key)
        app.echo(f"{key} reset to {app.cfg.get(key)!r}", "ok")
    else:
        value = app.cfg.set(key, val)
        app.echo(f"{key} = {value!r}", "ok")
    apply_setting(app, key)


DEVICE_SETTINGS = ("manualadd", "multiacks", "locpolicy", "pathhash", "autoadd", "telemetry")


def apply_setting(app, key: str) -> None:
    if key == "ui.theme":
        if app.cfg.get("ui.theme") in THEMES:
            app.theme_name = app.cfg.get("ui.theme")
            app.apply_theme()
    if key == "ui.nicklist":
        app.query_one("#nicklist").display = bool(app.cfg.get("ui.nicklist"))
    app.redraw()
    app.refresh_chrome()


def theme_swatch(name: str, current: bool) -> str:
    t = THEMES[name]
    bg = t["background"]
    nicks = "".join(f"[{c} on {bg}]■[/]" for c in t["nicks"][:8])
    bar = f"[{t['bar_fg']} on {t['bar_bg']}] [{t['bracket']} on {t['bar_bg']}][[/]12:34[{t['bracket']} on {t['bar_bg']}]][/] [/]"
    return (f"[{'bold ' if current else ''}{t['foreground']} on {bg}] {'▸' if current else ' '} {name:<17}[/]"
            f"[{t['timestamp']} on {bg}]12:34 [/][{t['own_nick']} on {bg}]<you>[/][{t['foreground']} on {bg}] hi [/]"
            f"[{t['hilight']}]@you[/][{bg} on {bg}] [/]{bar}[{bg} on {bg}] [/]{nicks}[{bg} on {bg}] [/]")


@command("theme", "client", "/theme [name|next|prev]", "Switch colour theme; no argument shows them all")
async def c_theme(app, args):
    names = list(THEMES)
    if args in ("next", "prev"):
        args = names[(names.index(app.theme_name) + (1 if args == "next" else -1)) % len(names)]
    if args not in THEMES:
        if args:
            app.echo(f"No theme called {args!r}.", "error")
        app.echo(f"{len(THEMES)} themes — /theme <name>, or /theme next and /theme prev to flip through them:")
        for name in names:
            app.echo_raw(theme_swatch(name, name == app.theme_name))
        return
    app.cfg["ui"]["theme"] = args
    app.cfg.save()
    apply_setting(app, "ui.theme")
    app.echo(f"Theme set to {args} ({names.index(args) + 1}/{len(names)}).", "ok")


@command("alias", "client", "/alias [name [/command args...]]", "Define a shortcut ($* = the arguments); no args lists them")
async def c_alias(app, args):
    name, _, body = args.partition(" ")
    aliases = app.cfg["aliases"]
    if not name:
        for k, v in sorted(aliases.items()):
            app.echo(f"/{k} → {v}")
        return
    name = name.lstrip("/").lower()
    if not body:
        app.echo(f"/{name} → {aliases.get(name, '(not defined)')}")
        return
    aliases[name] = body if body.startswith("/") else "/" + body
    app.cfg.save()
    app.echo(f"/{name} → {aliases[name]}", "ok")


@command("unalias", "client", "/unalias <name>", "Remove an alias")
async def c_unalias(app, args):
    app.cfg["aliases"].pop(args.lstrip("/").lower(), None)
    app.cfg.save()
    app.echo(f"Removed /{args.lstrip('/')}", "ok")


@command("notify", "client", "/notify [on|off|test]", "Desktop notifications for DMs and mentions")
async def c_notify(app, args):
    from ..notify import desktop_notify

    if args == "test":
        await desktop_notify("meshssi", "Notifications work 🎉")
        app.echo("Sent a test notification.", "ok")
        return
    if args in ("on", "off"):
        app.cfg["notify"]["desktop"] = args == "on"
        app.cfg.save()
    app.echo(f"Desktop notifications: {'on' if app.cfg.get('notify.desktop') else 'off'}"
             f" (only when unfocused: {app.cfg.get('notify.only_when_unfocused')}) — /notify test")


@command("plugins", "client", "/plugins", "List loaded plugins and their commands")
async def c_plugins(app, args):
    from ..config import PLUGIN_DIR

    app.echo(f"Plugin folder: {PLUGIN_DIR}")
    app.echo(f"Loaded: {', '.join(app.plugins.loaded) or 'none'}")
    for name, (_, cat, usage, text) in COMMANDS.items():
        if cat == "plugins":
            app.echo(f"  {usage}  {text}")


@command("reconnect", "client", "/reconnect [target]", "Reconnect, optionally to another radio (host[:port], /dev/..., ble:ADDR)",
         aliases=("connect", "server"))
async def c_reconnect(app, args):
    if app.mc:
        try:
            await asyncio.wait_for(app.mc.disconnect(), 3)
        except Exception:  # noqa: BLE001
            pass
    app.connected = False
    app.mc = None
    app.drops.clear()
    if args:
        app.target = args
        app.cfg["connection"]["target"] = args
        app.cfg.save()
    app.run_worker(app.connect(), exclusive=True, group="connect")


@command("quit", "client", "/quit", "Exit meshssi", aliases=("exit",))
async def c_quit(app, args):
    await app.action_quit()


