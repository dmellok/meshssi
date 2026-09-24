"""Administering remote nodes: repeaters, rooms and sensors."""

import time

from meshcore import EventType
from rich.markup import escape

from ..util import ago, fmt_duration
from . import command

PERMS = {0: "guest", 1: "read-only", 2: "read-write", 3: "admin"}


@command("login", "remote", "/login <node> [password]", "Log into a repeater or room (blank password = guest)")
async def c_login(app, args):
    c, pwd = app.split_target(args)
    c = c or app.need_contact("")
    if not c:
        return
    app.echo(f"Logging into {c['adv_name']}…")
    ev = await app.mc.commands.send_login_sync(c, pwd)
    if ev is not None and ev.type == EventType.LOGIN_SUCCESS:
        p = ev.payload
        level = PERMS.get(p.get("permissions", 3 if p.get("is_admin") else 0) & 3, "?")
        app.echo(f"Logged into {c['adv_name']} ({level})", "ok")
    else:
        app.echo("Login failed or timed out.", "error")


@command("logout", "remote", "/logout <node>", "Log out of a repeater or room")
async def c_logout(app, args):
    c = app.need_contact(args)
    if c:
        await app.cmd(app.mc.commands.send_logout(c))
        app.echo(f"Logged out of {c['adv_name']}.", "ok")


@command("rcmd", "remote", "/rcmd <node> <cli command>", "Run a CLI command on a repeater (log in first); the reply shows in its window",
         aliases=("rc",))
async def c_rcmd(app, args):
    c, cmd = app.split_target(args)
    if not c and app.win.kind == "query":
        c, cmd = app.contact(app.win.pubkey), args
    if not c or not cmd:
        app.echo("Usage: /rcmd <node> <command>, e.g. /rcmd Ridgeline Rpt get radio", "error")
        return
    win = app.query_window(c)
    app.add(win, {"k": "notice", "text": f"> {cmd}", "lvl": "dim"})
    await app.cmd(app.mc.commands.send_cmd(c, cmd))


@command("rstatus", "remote", "/rstatus <node>", "Request status (uptime, battery, airtime, counters) from a node", aliases=("status",))
async def c_rstatus(app, args):
    c = app.need_contact(args)
    if not c:
        return
    app.echo(f"Requesting status from {c['adv_name']}…")
    st = await app.mc.commands.req_status_sync(c)
    if not st:
        app.echo("No response (try /login first).", "error")
        return
    if c["public_key"] in app.dash:
        app.dash[c["public_key"]] = (time.time(), st)
    app.echo(f"[bold]{escape(c['adv_name'])}[/] status:", markup=True)
    for k, v in st.items():
        if k in ("pubkey_pre", "tag") or v is None:
            continue
        if k == "uptime":
            v = fmt_duration(v)
        elif k == "bat":
            v = f"{v / 1000:.2f} V"
        elif k in ("airtime", "rx_airtime"):
            v = f"{v}s ({100 * v / st['uptime']:.2f}%)" if st.get("uptime") else v
        app.echo(f"  {k:<18}: {v}")


@command("telemetry", "remote", "/telemetry [node]", "Sensor telemetry from a node (no node = this radio)", aliases=("tele",))
async def c_telemetry(app, args):
    if not args and app.win.kind != "query":
        ev = await app.cmd(app.mc.commands.get_self_telemetry())
        data, who = ev.payload.get("lpp", ev.payload), app.my_name
    else:
        c = app.need_contact(args)
        if not c:
            return
        app.echo(f"Requesting telemetry from {c['adv_name']}…")
        data, who = await app.mc.commands.req_telemetry_sync(c), c["adv_name"]
        if data is None:
            app.echo("No response.", "error")
            return
    if not data:
        app.echo(f"No telemetry from {who} (no sensors, or telemetry sharing is off — see /set telemetry).")
        return
    app.echo(f"Telemetry from {who}:")
    for item in data if isinstance(data, list) else [data]:
        if isinstance(item, dict):
            app.echo(f"  ch{item.get('channel', '?')} {item.get('type', '?')}: {item.get('value')}")
        else:
            app.echo(f"  {item}")


@command("neighbours", "remote", "/neighbours <repeater>", "A repeater's neighbours, with the SNR it hears them at",
         aliases=("neighbors", "nb"))
async def c_neighbours(app, args):
    c = app.need_contact(args)
    if not c:
        return
    app.echo(f"Requesting neighbours from {c['adv_name']}…")
    res = await app.mc.commands.fetch_all_neighbours(c)
    if not res:
        app.echo("No response (try /login first).", "error")
        return
    items = res.get("neighbours", []) if isinstance(res, dict) else res
    app.echo(f"{c['adv_name']} has {len(items)} neighbour(s):")
    for n in sorted(items, key=lambda n: -n.get("snr", -99)):
        key = n.get("pubkey", "")
        name = app.name_for(key) or key
        heard = time.time() - n["secs_ago"] if "secs_ago" in n else None
        app.echo(f"  {name:<28} snr {n.get('snr', 0):>+6.2f} dB  {ago(heard) if heard else ''}")


@command("acl", "remote", "/acl <repeater|room>", "List who has access to a node (you must be admin)")
async def c_acl(app, args):
    c = app.need_contact(args)
    if not c:
        return
    acl = await app.mc.commands.req_acl_sync(c)
    if acl is None:
        app.echo("No response (log in as admin first).", "error")
        return
    app.echo(f"Access list for {c['adv_name']} ({len(acl)} entries):")
    for e in acl:
        app.echo(f"  {(app.name_for(e['key']) or '?'):<28} {e['key']}  {PERMS.get(e['perm'] & 3, e['perm'])}")


def resolve_acl_target(app, who: str, level: int) -> tuple[str | None, str | None, str]:
    """Resolve the user for /setperm strictly: an exact contact or heard-node name, a full 64-hex key, or
    (only when removing someone, which the firmware allows by prefix) an unambiguous hex prefix of 8+ chars.
    No fuzzy matching: a near-miss must not land on someone else's key. Returns (key, name, error)."""
    from ..util import name_forms

    q = who.strip().lower()
    known = {k: c["adv_name"] for k, c in (app.mc.contacts.items() if app.mc else [])}
    for k, h in app.heard.items():
        known.setdefault(k, h.get("name", ""))
    by_name = [k for k, n in known.items() if n and (q == n.lower() or q in name_forms(n))]
    if len(by_name) == 1:
        return by_name[0], known[by_name[0]], ""
    if len(by_name) > 1:
        return None, None, f"{who!r} matches {len(by_name)} nodes; use a key prefix instead"
    if not q or any(ch not in "0123456789abcdef" for ch in q):
        return None, None, f"no contact or heard node is called exactly {who!r} (names must match in full here)"
    if len(q) == 64:
        return q, known.get(q), ""
    if level != 0:
        return None, None, ("granting access needs the full 64-character key (the firmware rejects prefixes); "
                            "use the contact's exact name or its full key")
    if len(q) < 8:
        return None, None, "key prefixes must be at least 8 hex characters"
    hits = [k for k in known if k.startswith(q)]
    if len(hits) > 1:
        return None, None, f"prefix {q} matches {len(hits)} known nodes; use more characters"
    return (hits[0] if hits else q), (known[hits[0]] if hits else None), ""


@command("setperm", "remote", "/setperm <node> <contact|key> <guest|read-only|read-write|admin>",
         "Change a user's permission on a repeater/room (asks for confirmation)")
async def c_setperm(app, args):
    node, rest = app.split_target(args)
    who, level_word = rest.rsplit(" ", 1) if " " in rest else (rest, "")
    levels = {v: k for k, v in PERMS.items()} | {str(k): k for k in PERMS}
    level = levels.get(level_word.lower())
    if not node or level is None or not who:
        app.echo("Usage: /setperm <node> <exact contact name or key> <guest|read-only|read-write|admin>  "
                 "(guest removes them from the access list)", "error")
        return
    key, name, err = resolve_acl_target(app, who, level)
    if err:
        app.echo(f"/setperm: {err}.", "error")
        return
    label = f"{name} ({key[:12]})" if name else key[:12] + ("…" if len(key) > 12 else "")
    if level == 0:
        action = f"remove {label} from {node['adv_name']}'s access list"
    else:
        action = f"make {label} {PERMS[level].upper() if level == 3 else PERMS[level]} on {node['adv_name']}"
    if key == app.self_info.get("public_key") and level < 3:
        action += " — this is YOU; you may lose admin access to this node"
    if not app.confirm(action):
        return
    win = app.query_window(node)
    app.add(win, {"k": "notice", "text": f"> setperm {key} {level}  ({label}: {PERMS[level]})", "lvl": "dim"})
    await app.cmd(app.mc.commands.send_cmd(node, f"setperm {key} {level}"))
    app.echo(f"Sent. The reply appears in {node['adv_name']}'s window; /acl {node['adv_name']} shows the result.", "ok")


@command("owner", "remote", "/owner <node>", "Ask a node for its owner info (no login needed)")
async def c_owner(app, args):
    c = app.need_contact(args)
    if c:
        res = await app.mc.commands.req_owner_sync(c)
        app.echo(f"{c['adv_name']}: {res['name']} — owner: {res['owner'] or '(not set)'}" if res else "No response.",
                 "info" if res else "error")


@command("regions", "remote", "/regions <node>", "Ask a repeater which flood-scope regions it serves")
async def c_regions(app, args):
    c = app.need_contact(args)
    if c:
        res = await app.mc.commands.req_regions_sync(c)
        app.echo(f"{c['adv_name']} regions: {res or '(none)'}" if res is not None else "No response.",
                 "info" if res is not None else "error")


@command("watch", "remote", "/watch <repeater>", "Add a repeater to the (dash) dashboard; its status is polled over the mesh")
async def c_watch(app, args):
    c = app.need_contact(args)
    if not c:
        return
    app.dash[c["public_key"]] = (0, None)
    app.save_state()
    win = app.special_window("view", "dash")
    app.switch(app.windows.index(win))
    app.run_worker(app.poll_repeater(c["public_key"]), group=f"dash-{c['public_key']}")


@command("unwatch", "remote", "/unwatch <repeater>", "Remove a repeater from the dashboard")
async def c_unwatch(app, args):
    c = app.need_contact(args)
    if c:
        app.dash.pop(c["public_key"], None)
        app.save_state()
        app.echo(f"Stopped watching {c['adv_name']}.", "ok")


@command("dash", "remote", "/dash [refresh]", "Open the repeater dashboard; refresh polls every watched repeater now")
async def c_dash(app, args):
    if args == "refresh":
        for key in app.dash:
            app.run_worker(app.poll_repeater(key), group=f"dash-{key}")
    app.switch(app.windows.index(app.special_window("view", "dash")))
