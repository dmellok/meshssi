"""Looking at the mesh itself: packet monitor, traces, discovery, map, graphs."""

import asyncio

from meshcore import EventType

from .. import geo
from ..util import ago
from . import command

TYPES = {0: "?", 1: "chat", 2: "repeater", 3: "room", 4: "sensor"}
TYPE_BITS = {"chat": 1 << 1, "repeater": 1 << 2, "room": 1 << 3, "sensor": 1 << 4}


@command("rf", "mesh", "/rf [clear|stats]", "Open the live packet monitor: every packet the radio hears", aliases=("monitor", "sniff"))
async def c_rf(app, args):
    if args == "stats":
        counts: dict[str, int] = {}
        for s in app.rf:
            counts[s["ptype"]] = counts.get(s["ptype"], 0) + 1
        span = app.rf[-1]["t"] - app.rf[0]["t"] if len(app.rf) > 1 else 0
        app.echo(f"{len(app.rf)} packets over {span / 60:.0f} min: "
                 + ", ".join(f"{k} {v}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1])))
        snrs = [s["snr"] for s in app.rf if s.get("snr") is not None]
        if snrs:
            app.echo(f"SNR avg {sum(snrs) / len(snrs):+.1f} dB, min {min(snrs):+.1f}, max {max(snrs):+.1f}")
        return
    win = app.special_window("rf")
    if args == "clear":
        win.recs.clear()
    app.switch(app.windows.index(win))


@command("map", "mesh", "/map [in|out|fit|center <node>|basemap on|off|style braille|dots|cache]",
         "Map of nodes over OpenStreetMap, with distance and bearing from you (arrows pan, +/- zoom)")
async def c_map(app, args):
    from ..views import MapState

    win = app.special_window("view", "map")
    if not hasattr(app, "map_state"):
        app.map_state = MapState(app)
    ms = app.map_state
    word, _, rest = args.partition(" ")
    if word in ("in", "out"):
        ms.zoom(2 if word == "in" else 0.5)
    elif word == "fit":
        ms.fit()
    elif word == "center":
        if rest in ("", "me"):
            if not app.my_pos:
                app.echo("Your node has no location; set one with /coords.", "error")
                return
            ms.center(*app.my_pos)
        else:
            node = app.find_contact(rest)
            h = next((h for h in app.heard.values() if rest.lower() == h.get("name", "").lower()), None)
            lat, lon = (node["adv_lat"], node["adv_lon"]) if node else ((h.get("lat"), h.get("lon")) if h else (None, None))
            if not geo.has_fix(lat, lon):
                app.echo(f"No location known for {rest!r}.", "error")
                return
            ms.center(lat, lon)
    elif word == "basemap" and rest in ("on", "off"):
        app.cfg["map"]["basemap"] = rest == "on"
        app.cfg.save()
        app.echo(f"OpenStreetMap background {rest}.", "ok")
    elif word == "style" and rest in ("braille", "dots"):
        app.cfg["map"]["style"] = rest
        app.cfg.save()
        app.echo(f"Map lines drawn with {rest}.", "ok")
    elif word == "cache":
        from ..basemap import CACHE_DIR

        files = list(CACHE_DIR.rglob("*.pbf")) if CACHE_DIR.exists() else []
        size = sum(f.stat().st_size for f in files)
        app.echo(f"{len(files)} map tiles cached ({size / 1e6:.1f} MB) in {CACHE_DIR}")
        return
    elif args:
        app.echo("Usage: /map [in|out|fit|center <node>|basemap on|off|style braille|dots|cache]", "error")
        return
    app.switch(app.windows.index(win))


@command("graphs", "mesh", "/graphs", "Noise floor, signal, traffic and per-node SNR over time", aliases=("signal",))
async def c_graphs(app, args):
    app.switch(app.windows.index(app.special_window("view", "graphs")))


@command("heard", "mesh", "/heard [filter]", "Every node heard over the air this session and before, contacts or not")
async def c_heard(app, args):
    nodes = sorted(app.heard.items(), key=lambda kv: -(kv[1].get("last") or 0))
    if args:
        nodes = [(k, h) for k, h in nodes if args.lower() in h.get("name", "").lower()]
    app.echo(f"{len(nodes)} node(s) heard")
    for k, h in nodes[:200]:
        known = "contact" if app.mc and k in app.mc.contacts else "       "
        snr = f"{h['snr']:+.1f}dB" if h.get("snr") is not None else ""
        app.echo(f"{TYPES.get(h.get('type'), '?'):<8} {h.get('name', '?'):<26} {k[:12]}  {known}  {ago(h.get('last')):<9} {snr}")


def trace_path_for(app, c: dict) -> list[str]:
    """Hashes for a round trip: out along the stored route to the node (if it repeats), and back."""
    width = (c.get("out_path_hash_mode", 0) + 1) * 2
    out = c.get("out_path", "") if c.get("out_path_len", -1) > 0 else ""
    hops = [out[i : i + width] for i in range(0, len(out), width)]
    if c["type"] in (2, 3):  # repeaters and rooms answer traces themselves
        hops.append(c["public_key"][:width])
    return hops + hops[-2::-1] if hops else []


@command("trace", "mesh", "/trace <node | hash,hash,...>", "Trace a route, showing the SNR at every hop")
async def c_trace(app, args):
    if not args:
        app.echo("Usage: /trace <repeater>   or   /trace a1b2,c3d4,a1b2 (explicit round trip)", "error")
        return
    if "," in args or (not app.find_contact(args) and all(ch in "0123456789abcdefABCDEF" for ch in args)
                       and len(args) in (2, 4, 8)):
        hops = [h.strip().lower() for h in args.split(",") if h.strip()]
        label = args
    else:
        c = app.need_contact(args)
        if not c:
            return
        hops = trace_path_for(app, c)
        if not hops:
            app.echo(f"{c['adv_name']} isn't a repeater and has no stored route — trace the repeater it's behind instead.", "error")
            return
        label = c["adv_name"]
    widths = {len(h) for h in hops}
    if len(widths) != 1 or widths.pop() not in (2, 4, 8):
        app.echo("All hashes in a trace must be the same length (1, 2 or 4 bytes).", "error")
        return
    app.echo(f"Tracing {label}: {' → '.join(app.resolve_hash(h) or h for h in hops)}")
    tag = None
    async with app.io:
        ev = await app.mc.commands.send_trace(path=",".join(hops))
    if ev.is_error():
        app.echo(f"Trace failed: {ev.payload}", "error")
        return
    tag = int.from_bytes(ev.payload["expected_ack"], "little")
    timeout = max(ev.payload.get("suggested_timeout", 10000) / 1000 * 1.5, 8)
    res = await app.mc.wait_for_event(EventType.TRACE_DATA, attribute_filters={"tag": tag}, timeout=timeout)
    if not res:
        app.echo(f"No trace reply after {timeout:.0f}s — a hop may be out of range.", "error")
        return
    nodes = res.payload.get("path", [])
    parts = [app.my_name]
    for n in nodes:
        who = app.resolve_hash(n["hash"]) or n["hash"] if "hash" in n else app.my_name
        parts.append(f"({n['snr']:+.2f} dB) → {who}")
    app.echo(" ".join(parts), "ok")
    weakest = min((n["snr"] for n in nodes), default=None)
    if weakest is not None:
        app.echo(f"weakest hop {weakest:+.2f} dB SNR · {len(nodes)} legs", "dim")


@command("discover", "mesh", "/discover [chat|repeater|room|sensor…]", "Ask nearby nodes to identify themselves (zero-hop)")
async def c_discover(app, args):
    kinds = args.split() or ["repeater"]
    bits = 0
    for k in kinds:
        if k not in TYPE_BITS:
            app.echo(f"Unknown type {k}; use chat, repeater, room, sensor", "error")
            return
        bits |= TYPE_BITS[k]
    found: list[dict] = []

    def on_resp(ev):
        found.append(ev.payload)

    sub = app.mc.subscribe(EventType.DISCOVER_RESPONSE, on_resp)
    try:
        async with app.io:
            ev = await app.mc.commands.send_node_discover_req(bits, prefix_only=False)
        if ev is None or ev.is_error():
            app.echo(f"Discovery request failed: {ev.payload if ev else 'no reply'}", "error")
            return
        app.echo(f"Discovering nearby {'/'.join(kinds)} nodes for 10s…")
        await asyncio.sleep(10)
    finally:
        sub.unsubscribe()
    tag = ev.payload.get("tag")
    tag_hex = tag.to_bytes(4, "little").hex() if isinstance(tag, int) else str(tag)
    found = [f for f in found if f.get("tag") in (tag_hex, None)] or found
    if not found:
        app.echo("Nobody answered. (Nodes only answer discovery if their firmware supports it.)")
        return
    for f in sorted(found, key=lambda f: -f.get("SNR_in", -99)):
        key = f.get("pubkey", "")
        name = app.name_for(key) or "(unknown — wait for its advert)"
        app.echo(f"{TYPES.get(f.get('node_type'), '?'):<8} {name:<28} {key[:12]}  heard us at {f.get('SNR_in', 0):+.1f} dB, "
                 f"we heard it at {f.get('SNR', 0):+.1f} dB" if "SNR" in f else
                 f"{TYPES.get(f.get('node_type'), '?'):<8} {name:<28} {key[:12]}  heard us at {f.get('SNR_in', 0):+.1f} dB")


@command("scope", "mesh", "/scope [region|*|off] [-default]", "Flood scope: limit floods to a region (-default saves it on the radio)")
async def c_scope(app, args):
    default = args.endswith("-default")
    scope = args.removesuffix("-default").strip()
    c = app.mc.commands
    if not scope:
        ev = await app.cmd(c.get_default_flood_scope())
        name = ev.payload.get("scope_name") or "none (unscoped)"
        app.echo(f"Default flood scope on the radio: {name}")
        return
    value = None if scope in ("off", "none", "0") else scope
    if default:
        await app.cmd(c.set_default_flood_scope(value))
        app.echo(f"Default flood scope set to {value or 'none'}.", "ok")
    else:
        await app.cmd(c.set_flood_scope(value))
        app.echo(f"Flood scope for this session: {value or 'default'}.", "ok")


