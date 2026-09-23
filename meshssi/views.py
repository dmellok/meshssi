"""Full-window views: the node map, signal graphs, and the repeater dashboard."""

import asyncio
import time

from rich.text import Text

from . import geo
from .util import ago, fmt_duration, sparkline

TYPE_NAMES = {0: "?", 1: "chat", 2: "repeater", 3: "room", 4: "sensor"}
DEFAULT_TILES = "https://tiles.openfreemap.org/planet"


def _nodes(app) -> list[dict]:
    nodes = {}
    for key, h in app.heard.items():
        nodes[key] = {"name": h.get("name", key[:8]), "type": h.get("type", 0), "lat": h.get("lat"), "lon": h.get("lon"),
                      "last": h.get("last"), "snr": h.get("snr"), "contact": False}
    for key, c in (app.mc.contacts.items() if app.mc else []):
        n = nodes.setdefault(key, {})
        n.update(name=c["adv_name"], type=c["type"], contact=True, last=max(n.get("last") or 0, c.get("last_advert") or 0))
        if geo.has_fix(c.get("adv_lat"), c.get("adv_lon")):
            n.update(lat=c["adv_lat"], lon=c["adv_lon"])
    return list(nodes.values())


class MapState:
    """Where the map is looking (auto-fit, or wherever you've panned/zoomed), plus the basemap tiles and a
    cache of the last rendered background, which is drawn off the UI thread."""

    def __init__(self, app):
        from .basemap import TileSource

        self.app = app
        self.user: tuple[float, float, float] | None = None  # (cx, cy, scale) once you pan or zoom
        self.last = None  # the View drawn last time, the starting point for pan/zoom
        self.tiles = TileSource(app.cfg.get("map.tiles") or DEFAULT_TILES, on_update=self._tile_arrived)
        self.cache: tuple | None = None  # (key, canvas)
        self.rendering: tuple | None = None

    def _tile_arrived(self) -> None:
        if self.app.win.kind == "view" and self.app.win.view == "map":
            self.app.call_after_refresh(self.app.refresh_view)

    def view(self, cols: int, rows: int, points: list[tuple[float, float]]):
        from .basemap import View

        if self.user:
            v = View(*self.user, cols, rows)
        elif points:
            v = View.fit(points, cols, rows)
        else:
            return None
        self.last = v
        return v

    # navigation
    def pan(self, dx: float, dy: float) -> None:
        if self.last:
            w, h = self.last.dots
            self.user = (self.last.cx + dx * w / self.last.scale, self.last.cy + dy * h / self.last.scale, self.last.scale)

    def zoom(self, factor: float) -> None:
        if self.last:
            self.user = (self.last.cx, self.last.cy, max(64.0, min(2**24, self.last.scale * factor)))

    def center(self, lat: float, lon: float) -> None:
        from .basemap import to_world

        scale = self.last.scale if self.last else 2**15
        self.user = (*to_world(lat, lon), max(scale, 2**14))

    def fit(self) -> None:
        self.user = None

    # background
    def canvas(self, view) -> tuple[object | None, str]:
        """The rendered basemap for this view if ready, and a one-line status for the footer."""
        from .basemap import ATTRIBUTION, draw

        cfg = self.app.cfg
        if not cfg.get("map.basemap", True):
            return None, "basemap off (/map basemap on)"
        _, tiles = self.tiles.tiles_for(view)
        have = [t for t in tiles if t[3] is not None]
        missing = len(tiles) - len(have)
        if not have:
            if self.tiles.status == "offline":
                return None, "basemap unavailable offline; areas you've viewed before are cached"
            return None, "loading OpenStreetMap tiles…"
        key = (round(view.cx, 10), round(view.cy, 10), round(view.scale, 4), view.cols, view.rows,
               self.app.theme_name, tuple(sorted(t[:3] for t in have)))
        status = ATTRIBUTION + (f" · {missing} tile(s) {'offline' if self.tiles.status == 'offline' else 'loading'}" if missing else "")
        if self.cache and self.cache[0] == key:
            return self.cache[1], status
        if self.rendering != key:
            self.rendering = key
            colors = self.app.st["map"]

            async def render():
                try:
                    cv = await asyncio.to_thread(draw, view, tiles, colors)
                except Exception:  # noqa: BLE001 - odd tile data: carry on without a background
                    cv = None
                self.cache = (key, cv)
                self.rendering = None
                self._tile_arrived()

            asyncio.get_running_loop().create_task(render())
        stale = self.cache and self.cache[0][:6] == key[:6]  # same view, fewer tiles: still lines up
        return (self.cache[1] if stale else None), status


def render_map(app, width: int, height: int) -> Text:
    st = app.st
    nodes = _nodes(app)
    me = app.my_pos
    table_rows = min(12, max(4, height // 3))
    map_rows = max(6, height - table_rows - 3)
    if not hasattr(app, "map_state"):
        app.map_state = MapState(app)
    ms = app.map_state
    local, far = geo.local_points(me, nodes)
    view = ms.view(width, map_rows, [(n["lat"], n["lon"]) for n in local] + ([me] if me else []))
    out = Text()
    if view is None:
        out.append("No nodes with a location yet. Adverts that carry GPS positions will appear here.\n", st["dim"])
        return out
    canvas, status = ms.canvas(view)
    style = app.cfg.get("map.style", "braille")
    text, off = geo.render_map(view, (me[0], me[1], app.my_name) if me else None, nodes, app.nick_color, st,
                               canvas=canvas, dot_style="dots" if style == "dots" else "braille")
    out.append(text)
    located = [n for n in nodes if geo.has_fix(n.get("lat"), n.get("lon"))]
    out.append(f"◉ you  R repeater  @ chat  # room  S sensor  ·  {len(located) - off} on map"
               + (f"  ·  {off} off screen" if off else "") + ("  ·  zoomed" if ms.user else ""), st["dim"])
    out.append("\n")
    out.append("arrows pan · + - zoom · 0 fit · c you   ", st["meta"])
    out.append(status + "\n", st["meta"])
    if me:
        located.sort(key=lambda n: geo.distance_km(*me, n["lat"], n["lon"]))
    out.append(f"{'node':<24} {'type':<9} {'distance':>10}  {'bearing':<8} {'heard':<10} snr\n", "bold")
    for n in located[: table_rows - 1]:
        dist = geo.fmt_distance(geo.distance_km(*me, n["lat"], n["lon"])) if me else "?"
        brg = geo.compass(geo.bearing(*me, n["lat"], n["lon"])) if me else ""
        out.append(f"{n['name'][:23]:<24} ", app.nick_color(n["name"]))
        out.append(f"{TYPE_NAMES.get(n['type'], '?'):<9} {dist:>10}  {brg:<8} {ago(n.get('last')):<10} ")
        out.append(f"{n['snr']:+.1f}" if n.get("snr") is not None else "", st["dim"])
        out.append("\n")
    if not me:
        out.append("Set your own location with /coords to get distances and bearings.\n", st["dim"])
    return out


def _series(samples, key) -> list[float]:
    return [s[key] for s in samples if key in s]


def _rate(samples, key) -> list[float]:
    """Per-minute rate from a cumulative counter."""
    out = []
    prev = None
    for s in samples:
        if key not in s:
            continue
        if prev is not None and s["t"] > prev["t"] and s[key] >= prev[key]:
            out.append((s[key] - prev[key]) * 60 / (s["t"] - prev["t"]))
        prev = s
    return out


def _graph_row(out: Text, label: str, values: list[float], width: int, unit: str, style: str, st: dict) -> None:
    out.append(f"{label:<18}", "bold")
    if not values:
        out.append("waiting for data…\n", st["dim"])
        return
    spark_w = max(10, width - 18 - 44)
    out.append(sparkline(values, spark_w).ljust(spark_w), style)
    out.append(f"  now {values[-1]:>7.1f}{unit}  min {min(values):>6.1f}  max {max(values):>6.1f}\n", st["dim"])


def render_graphs(app, width: int, height: int) -> Text:
    st = app.st
    s = sorted(app.samples, key=lambda x: x["t"])
    out = Text()
    span = f"{fmt_duration(s[-1]['t'] - s[0]['t'])}" if len(s) > 1 else "just started"
    out.append(f"Radio health · {len(s)} samples every 30s · span {span}\n\n", st["dim"])
    _graph_row(out, "noise floor", _series(s, "noise_floor"), width, "dBm", st["graph"][0], st)
    _graph_row(out, "last RSSI", _series(s, "last_rssi"), width, "dBm", st["graph"][1], st)
    _graph_row(out, "last SNR", _series(s, "last_snr"), width, "dB", st["graph"][2], st)
    _graph_row(out, "packets rx /min", _rate(s, "recv"), width, "", st["graph"][3], st)
    _graph_row(out, "packets tx /min", _rate(s, "sent"), width, "", st["graph"][4], st)
    _graph_row(out, "tx airtime s/min", _rate(s, "tx_air_secs"), width, "s", st["graph"][5], st)
    _graph_row(out, "rx airtime s/min", _rate(s, "rx_air_secs"), width, "s", st["graph"][6], st)
    bat = [v / 1000 for v in _series(s, "battery_mv") if v]
    if bat:
        _graph_row(out, "battery", bat, width, "V", st["graph"][7], st)
    out.append("\nSNR per node (from messages and adverts)\n", "bold underline")
    rows = sorted(app.snr_hist.items(), key=lambda kv: -kv[1][-1][0])[: max(1, height - 16)]
    if not rows:
        out.append("Nothing heard yet.\n", st["dim"])
    for name, hist in rows:
        vals = [v for _, v in hist]
        out.append(f"{name[:17]:<18}", app.nick_color(name))
        spark_w = max(10, width - 18 - 44)
        out.append(sparkline(vals, spark_w).ljust(spark_w), app.nick_color(name))
        out.append(f"  now {vals[-1]:>+7.1f}dB  n={len(vals):<4} {ago(hist[-1][0])}\n", st["dim"])
    return out


def render_dash(app, width: int, height: int) -> Text:
    st = app.st
    out = Text()
    if not app.dash:
        out.append("No repeaters watched. /watch <repeater> adds one (log in first with /login if it needs a password).\n"
                   "Their status is polled over the mesh every dashboard.interval minutes.", st["dim"])
        return out
    out.append(f"{'repeater':<20} {'battery':>7} {'uptime':>10} {'noise':>6} {'rssi':>5} {'snr':>6} "
               f"{'rx':>8} {'tx':>7} {'air%':>6}  updated\n", "bold")
    for key, (t, stt) in app.dash.items():
        name = app.name_for(key) or key[:12]
        out.append(f"{name[:19]:<20} ", app.nick_color(name))
        if not stt:
            out.append("waiting for first reply…" if not t or time.time() - t < 60 else "no reply", st["dim"])
            out.append("\n")
            continue
        up = stt.get("uptime", 0)
        air = 100 * stt.get("airtime", 0) / up if up else 0
        bat = f"{stt['bat'] / 1000:.2f}V" if stt.get("bat") else "?"
        out.append(f"{bat:>7} ", st["bad"] if stt.get("bat") and stt["bat"] < 3500 else "")
        d, rem = divmod(int(up), 86400)
        uptime = f"{d}d {rem // 3600:02d}h" if d else f"{rem // 3600}h {rem % 3600 // 60:02d}m"
        out.append(f"{uptime:>10} {stt.get('noise_floor', '?'):>6} {stt.get('last_rssi', '?'):>5} "
                   f"{stt.get('last_snr', 0):>+6.1f} {stt.get('nb_recv', '?'):>8} {stt.get('nb_sent', '?'):>7} ")
        out.append(f"{air:>5.2f}%  ", st["bad"] if air > 10 else "")
        out.append(f"{ago(t)}\n", st["dim"])
    out.append(f"\nPolled every {app.cfg.get('dashboard.interval', 10)} min · /watch <node> · /unwatch <node> · "
               "/dash refresh polls now", st["dim"])
    return out
