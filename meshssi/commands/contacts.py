"""Contacts: listing, adding, removing, routes, and sharing by URI / QR / file."""

import json
import time
from pathlib import Path

from rich.markup import escape

from .. import geo
from ..util import ago, name_forms
from . import command

TYPES = {0: "?", 1: "chat", 2: "repeater", 3: "room", 4: "sensor"}


@command("contacts", "contacts", "/contacts [filter]", "List contacts stored on the radio", aliases=("who", "names"))
async def c_contacts(app, args):
    await app.refresh_contacts()
    cs = sorted(app.mc.contacts.values(), key=lambda c: -c.get("last_advert", 0))
    if args:
        cs = [c for c in cs if args.lower() in c["adv_name"].lower() or args.lower() == TYPES.get(c["type"])]
    app.echo(f"{len(cs)} contact(s) (of {len(app.mc.contacts)}; radio holds up to {app.device_info.get('max_contacts', '?')})")
    for c in cs:
        dist = geo.describe(app.my_pos, c.get("adv_lat"), c.get("adv_lon"))
        app.echo(f"{TYPES.get(c['type'], '?'):<8} {c['adv_name']:<26} {c['public_key'][:12]}  "
                 f"{app.path_str(c):<24} {ago(c.get('last_advert')):<9} {dist}")
    app.refresh_nicklist()


@command("whois", "contacts", "/whois <contact>", "Show everything known about a node", aliases=("wi",))
async def c_whois(app, args):
    heard = next((h | {"key": k} for k, h in app.heard.items() if args.lower() in name_forms(h.get("name", ""))), None)
    c = app.find_contact(args) if args else None
    if not c and heard:
        where = geo.describe(app.my_pos, heard.get("lat"), heard.get("lon"))
        app.echo(f"{heard['name']} ({TYPES.get(heard.get('type'), '?')}, {heard['key'][:12]}) is heard over the air but isn't "
                 f"in your contacts. Last heard {ago(heard.get('last'))}"
                 + (f" at {heard['snr']:+.1f} dB" if heard.get("snr") is not None else "") + (f", {where}" if where else "")
                 + ". /accept it if it's /pending.")
        return
    c = c or app.need_contact(args)
    if not c:
        return
    app.echo(f"[bold]{escape(c['adv_name'])}[/]", markup=True)
    app.echo(f"  type      : {TYPES.get(c['type'], '?')}")
    app.echo(f"  key       : {c['public_key']}")
    app.echo(f"  route     : {app.path_str(c)}")
    app.echo(f"  advert    : {ago(c.get('last_advert'))}")
    if geo.has_fix(c.get("adv_lat"), c.get("adv_lon")):
        app.echo(f"  location  : {c['adv_lat']:.5f}, {c['adv_lon']:.5f}  {geo.describe(app.my_pos, c['adv_lat'], c['adv_lon'])}")
    if h := app.heard.get(c["public_key"]):
        if h.get("snr") is not None:
            app.echo(f"  last heard: {ago(h.get('last'))} at {h['snr']:+.1f} dB SNR / {h.get('rssi')} dBm, {h.get('hops', '?')} hop(s)")
    if hist := app.snr_hist.get(c["adv_name"]):
        vals = [v for _, v in hist]
        app.echo(f"  snr       : avg {sum(vals) / len(vals):+.1f} dB over {len(vals)} packets (min {min(vals):+.1f}, max {max(vals):+.1f})")
    app.echo(f"  flags     : {c.get('flags', 0):#04x}{' (favourite)' if c.get('flags', 0) & 1 else ''}")
    try:
        ev = await app.cmd(app.mc.commands.get_advert_path(c))
        if ev.payload.get("path"):
            app.echo(f"  advert via: {ev.payload.get('path')}")
    except Exception:  # noqa: BLE001 - older firmware, or never heard directly
        pass


@command("pending", "contacts", "/pending", "Nodes heard but not yet added (your radio is in manual-add mode)")
async def c_pending(app, args):
    pend = app.mc.pending_contacts
    if not pend:
        app.echo("No pending contacts heard this session.")
    for c in pend.values():
        dist = geo.describe(app.my_pos, c.get("adv_lat"), c.get("adv_lon"))
        app.echo(f"{TYPES.get(c['type'], '?'):<8} {c['adv_name']:<26} {c['public_key'][:12]}  {ago(c.get('last_advert'))} {dist}")


@command("accept", "contacts", "/accept <name|key-prefix|all>", "Add a pending node to the radio's contacts", aliases=("add",))
async def c_accept(app, args):
    pend = list(app.mc.pending_contacts.values())
    q = args.lower()
    picks = pend if q == "all" else [c for c in pend if c["adv_name"].lower().startswith(q) or c["public_key"].startswith(q)]
    if not picks:
        app.echo("No matching pending contact. See /pending.", "error")
        return
    for c in picks:
        await app.cmd(app.mc.commands.add_contact(c))
        app.mc.pop_pending_contact(c["public_key"])
        app.status(f"Added {c['adv_name']}", "join")
    await app.refresh_contacts()
    app.refresh_nicklist()


@command("rmcontact", "contacts", "/rmcontact <contact>", "Delete a contact from the radio")
async def c_rmcontact(app, args):
    c = app.need_contact(args)
    if c and app.confirm(f"rmcontact {c['adv_name']}"):
        await app.cmd(app.mc.commands.remove_contact(c))
        app.mc.contacts.pop(c["public_key"], None)
        app.status(f"Removed {c['adv_name']}")
        app.refresh_nicklist()


@command("fav", "contacts", "/fav <contact>", "Toggle a contact's favourite flag (protects it from being dropped)")
async def c_fav(app, args):
    c = app.need_contact(args)
    if c:
        flags = c.get("flags", 0) ^ 1
        await app.cmd(app.mc.commands.change_contact_flags(c, flags))
        c["flags"] = flags
        app.echo(f"{c['adv_name']} is {'now' if flags & 1 else 'no longer'} a favourite.", "ok")


@command("resetpath", "contacts", "/resetpath <contact>", "Forget the stored route; the next message floods")
async def c_resetpath(app, args):
    c = app.need_contact(args)
    if c:
        await app.cmd(app.mc.commands.reset_path(c))
        await app.refresh_contacts()
        app.echo(f"Route to {c['adv_name']} reset to flood.", "ok")


@command("path", "contacts", "/path <contact>", "Discover a route to a contact and back (path discovery)", aliases=("ping", "pathfind"))
async def c_path(app, args):
    c = app.need_contact(args)
    if not c:
        return
    app.echo(f"Discovering path to {c['adv_name']}…")
    t0 = time.time()
    ev = await app.mc.commands.send_path_discovery_sync(c)
    if not ev:
        app.echo("No response.", "error")
        return
    p = ev.payload

    def fmt(path, n, hl):
        hops = [path[i : i + hl * 2] for i in range(0, len(path), hl * 2)]
        return (",".join(app.resolve_hash(h) or h for h in hops) or "direct") + f" ({n} hop{'s' if n != 1 else ''})"

    app.echo(f"Path to {c['adv_name']} ({time.time() - t0:.1f}s): out {fmt(p.get('out_path', ''), p.get('out_path_len', 0), p.get('out_path_hash_len', 1))}"
             f" · back {fmt(p.get('in_path', ''), p.get('in_path_len', 0), p.get('in_path_hash_len', 1))}", "ok")
    await app.refresh_contacts()


@command("share", "contacts", "/share <contact>", "Re-broadcast a contact's advert zero-hop so neighbours learn it")
async def c_share(app, args):
    c = app.need_contact(args)
    if c:
        await app.cmd(app.mc.commands.share_contact(c))
        app.echo(f"Shared {c['adv_name']}.", "ok")


async def contact_uri(app, c: dict | None) -> str:
    ev = await app.cmd(app.mc.commands.export_contact(c))
    return ev.payload["uri"]


@command("uri", "contacts", "/uri [contact]", "Show a meshcore:// card for a contact, or for yourself")
async def c_uri(app, args):
    c = app.need_contact(args) if args else None
    if args and not c:
        return
    uri = await contact_uri(app, c)
    app.echo(f"{c['adv_name'] if c else app.my_name}: {uri}")


@command("qr", "contacts", "/qr [contact]", "Show a scannable QR code of a contact card (or your own) for the phone app")
async def c_qr(app, args):
    import segno

    c = app.need_contact(args) if args else None
    if args and not c:
        return
    uri = await contact_uri(app, c)
    rows = [list(r) for r in segno.make(uri, error="l").matrix_iter(border=2)]
    if len(rows) % 2:
        rows.append([0] * len(rows[0]))
    app.echo(f"Contact card for {c['adv_name'] if c else app.my_name} — scan with the MeshCore app:")
    for top, bottom in zip(rows[0::2], rows[1::2]):
        line = "".join({(1, 1): "█", (1, 0): "▀", (0, 1): "▄", (0, 0): " "}[(bool(a), bool(b))] for a, b in zip(top, bottom))
        app.echo_raw(f"[black on white]{line}[/]")


@command("import", "contacts", "/import <meshcore://… | file.json>", "Add a contact from a card URI, or contacts+channels from an /export file")
async def c_import(app, args):
    if args.startswith("meshcore://"):
        await app.cmd(app.mc.commands.import_contact(bytes.fromhex(args[len("meshcore://"):])))
        await app.refresh_contacts()
        app.echo("Contact imported.", "ok")
        return
    path = Path(args).expanduser()
    if not path.exists():
        app.echo("Usage: /import meshcore://<hex>   or   /import <file.json from /export>", "error")
        return
    data = json.loads(path.read_text())
    added = 0
    for entry in data.get("contacts", []):
        if entry.get("key") in app.mc.contacts:
            continue
        try:
            await app.cmd(app.mc.commands.import_contact(bytes.fromhex(entry["uri"][len("meshcore://"):])))
            added += 1
        except RuntimeError as e:
            app.echo(f"  {entry.get('name')}: {e}", "error")
    joined = 0
    for ch in data.get("channels", []):
        if any(c["channel_name"] == ch["name"] for c in app.channels.values()):
            continue
        free = next((i for i in range(1, app.device_info.get("max_channels", 8)) if i not in app.channels), None)
        if free is None:
            app.echo("Out of channel slots.", "error")
            break
        await app.cmd(app.mc.commands.set_channel(free, ch["name"], bytes.fromhex(ch["secret"])))
        app.channels[free] = {"channel_name": ch["name"]}
        joined += 1
    await app.refresh_contacts()
    await app.load_channels()
    app.echo(f"Imported {added} contact(s) and {joined} channel(s) from {path}.", "ok")


@command("export", "contacts", "/export <file.json>", "Save all contacts (as cards) and channels (with keys) to a file")
async def c_export(app, args):
    if not args:
        app.echo("Usage: /export ~/meshssi-backup.json", "error")
        return
    out = {"exported": time.strftime("%Y-%m-%d %H:%M:%S"), "node": app.my_name, "contacts": [], "channels": []}
    for c in app.mc.contacts.values():
        try:
            out["contacts"].append({"name": c["adv_name"], "key": c["public_key"], "type": TYPES.get(c["type"]),
                                    "uri": await contact_uri(app, c)})
        except RuntimeError:
            pass
    for idx, ch in sorted(app.channels.items()):
        out["channels"].append({"slot": idx, "name": ch["channel_name"], "secret": ch["channel_secret"].hex()})
    path = Path(args).expanduser()
    path.write_text(json.dumps(out, indent=2, ensure_ascii=False))
    path.chmod(0o600)
    app.echo(f"Exported {len(out['contacts'])} contacts and {len(out['channels'])} channels to {path} "
             "(contains channel keys — keep it private).", "ok")


@command("autoadd", "contacts", "/autoadd [on|off]", "Whether new nodes are added automatically (off = /pending + /accept)")
async def c_autoadd(app, args):
    if args in ("on", "off"):
        await app.cmd(app.mc.commands.set_manual_add_contacts(args == "off"))
        await app.cmd(app.mc.commands.send_appstart())
    manual = app.self_info.get("manual_add_contacts")
    app.echo(f"Auto-add contacts: {'off (manual: /pending, /accept)' if manual else 'on'}", "ok" if args else "info")


