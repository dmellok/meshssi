"""OpenStreetMap background for /map: vector tiles drawn with braille characters.

Tiles come from OpenFreeMap (OpenMapTiles schema, no API key) and are cached on disk, so areas you have
viewed keep working offline. Everything here degrades quietly: a tile that can't be fetched or decoded is
just left out, and with no tiles at all the map falls back to plain markers.
"""

import asyncio
import gzip
import json
import math
import os
import time
import urllib.request
from pathlib import Path

CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "meshssi" / "tiles"
DEFAULT_SOURCE = "https://tiles.openfreemap.org/planet"
ATTRIBUTION = "© OpenStreetMap contributors · OpenFreeMap"
USER_AGENT = "meshssi (+https://github.com/dmellok/meshssi)"
MAX_TILE_ZOOM = 14
TILEJSON_TTL = 86400
RETRY_AFTER = 300  # seconds before retrying a tile that failed to download


# ── Mapbox Vector Tile decoding (just enough of protobuf and the MVT spec) ──────────────────────────
MAX_TILE_BYTES = 8 << 20  # refuse anything bigger, compressed or not


def _varint(buf: bytes, i: int) -> tuple[int, int]:
    shift = result = 0
    while True:
        if shift > 63:
            raise ValueError("varint too long")
        b = buf[i]
        i += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, i
        shift += 7


def _fields(buf: bytes):
    """Yield (field number, wire type, value) for a protobuf message."""
    i, n = 0, len(buf)
    while i < n:
        key, i = _varint(buf, i)
        field, wire = key >> 3, key & 7
        if wire == 0:
            val, i = _varint(buf, i)
        elif wire == 2:
            size, i = _varint(buf, i)
            val, i = buf[i : i + size], i + size
        elif wire == 1:
            val, i = buf[i : i + 8], i + 8
        elif wire == 5:
            val, i = buf[i : i + 4], i + 4
        else:
            raise ValueError(f"unsupported wire type {wire}")
        yield field, wire, val


def _packed(buf: bytes) -> list[int]:
    out, i = [], 0
    while i < len(buf):
        v, i = _varint(buf, i)
        out.append(v)
    return out


def _value(buf: bytes):
    import struct

    for field, wire, val in _fields(buf):
        if field == 1:
            return val.decode("utf-8", "replace")
        if field == 2:
            return struct.unpack("<f", val)[0]
        if field == 3:
            return struct.unpack("<d", val)[0]
        if field in (4, 5):
            return val
        if field == 6:
            return (val >> 1) ^ -(val & 1)
        if field == 7:
            return bool(val)
    return None


def _geometry(cmds: list[int]) -> list[list[tuple[int, int]]]:
    parts: list[list[tuple[int, int]]] = []
    x = y = i = 0
    while i < len(cmds):
        cid, count = cmds[i] & 7, cmds[i] >> 3
        i += 1
        if cid == 7:  # ClosePath
            if parts and parts[-1]:
                parts[-1].append(parts[-1][0])
            continue
        for _ in range(count):
            dx, dy = cmds[i], cmds[i + 1]
            i += 2
            x += (dx >> 1) ^ -(dx & 1)
            y += (dy >> 1) ^ -(dy & 1)
            if cid == 1:
                parts.append([(x, y)])
            elif parts:
                parts[-1].append((x, y))
    return parts


def decode_tile(data: bytes, want: set[str] | None = None) -> dict[str, list[dict]]:
    """Decode an MVT tile into {layer: [{"type", "props", "parts", "extent"}]}."""
    data = _gunzip(data)
    layers: dict[str, list[dict]] = {}
    for field, _, layer_buf in _fields(data):
        if field != 3:
            continue
        name, extent, keys, values, raw = "", 4096, [], [], []
        for f, _, v in _fields(layer_buf):
            if f == 1:
                name = v.decode()
            elif f == 2:
                raw.append(v)
            elif f == 3:
                keys.append(v.decode())
            elif f == 4:
                values.append(_value(v))
            elif f == 5:
                extent = v
        if want is not None and name not in want:
            continue
        feats = []
        for fbuf in raw:
            tags, gtype, geom = [], 0, []
            for f, _, v in _fields(fbuf):
                if f == 2:
                    tags = _packed(v)
                elif f == 3:
                    gtype = v
                elif f == 4:
                    geom = _packed(v)
            props = {keys[tags[j]]: values[tags[j + 1]] for j in range(0, len(tags) - 1, 2)
                     if tags[j] < len(keys) and tags[j + 1] < len(values)}
            feats.append({"type": gtype, "props": props, "parts": _geometry(geom), "extent": extent})
        layers[name] = feats
    return layers


# ── projection ────────────────────────────────────────────────────────────────────────────────────────
def to_world(lat: float, lon: float) -> tuple[float, float]:
    """Web Mercator, normalised to 0..1 on both axes."""
    lat = max(-85.0511, min(85.0511, lat))
    s = math.sin(math.radians(lat))
    return (lon + 180) / 360, 0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)


class View:
    """What part of the world is on screen: a centre in world units and a scale in braille dots per unit.

    A braille character is 2×4 dots and a terminal cell is about twice as tall as it is wide, so dots
    come out roughly square and the same scale works for both axes.
    """

    def __init__(self, cx: float, cy: float, scale: float, cols: int, rows: int):
        self.cx, self.cy, self.scale, self.cols, self.rows = cx, cy, scale, cols, rows

    @property
    def dots(self) -> tuple[int, int]:
        return self.cols * 2, self.rows * 4

    def to_dot(self, wx: float, wy: float) -> tuple[float, float]:
        w, h = self.dots
        return (wx - self.cx) * self.scale + w / 2, (wy - self.cy) * self.scale + h / 2

    def to_cell(self, wx: float, wy: float) -> tuple[int, int]:
        dx, dy = self.to_dot(wx, wy)
        return int(dx // 2), int(dy // 4)

    def bounds(self) -> tuple[float, float, float, float]:
        w, h = self.dots
        return (self.cx - w / 2 / self.scale, self.cy - h / 2 / self.scale,
                self.cx + w / 2 / self.scale, self.cy + h / 2 / self.scale)

    def tile_zoom(self) -> int:
        # about 160 dots per tile: detail to match what the braille grid can show, a handful of tiles per screen
        return max(0, min(MAX_TILE_ZOOM, int(math.log2(max(self.scale, 1) / 160) + 0.5)))

    @classmethod
    def fit(cls, points: list[tuple[float, float]], cols: int, rows: int, margin: float = 0.12) -> "View":
        ws = [to_world(lat, lon) for lat, lon in points]
        xs, ys = [w[0] for w in ws], [w[1] for w in ws]
        span_x, span_y = max(max(xs) - min(xs), 1e-6), max(max(ys) - min(ys), 1e-6)
        w, h = cols * 2 * (1 - 2 * margin), rows * 4 * (1 - 2 * margin)
        scale = min(w / span_x, h / span_y, 2 ** 22)  # don't zoom past street level for a single node
        return cls((max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2, scale, cols, rows)


# ── tile source: memory → disk cache → network (in the background) ───────────────────────────────
def fetch_url(url: str, timeout: float = 10) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"})
    if not url.startswith("https://"):
        raise ValueError(f"only https tile sources are allowed, not {url[:40]!r}")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read(MAX_TILE_BYTES + 1)
    if len(data) > MAX_TILE_BYTES:
        raise ValueError("tile too large")
    return _gunzip(data)


def _gunzip(data: bytes) -> bytes:
    if data[:2] != b"\x1f\x8b":
        return data
    with gzip.GzipFile(fileobj=__import__("io").BytesIO(data)) as g:
        out = g.read(MAX_TILE_BYTES + 1)
    if len(out) > MAX_TILE_BYTES:
        raise ValueError("tile decompresses too large")
    return out


def _fetch(url: str) -> bytes:
    return fetch_url(url)  # looked up at call time, so tests can swap fetch_url out


LAYERS = {"water", "waterway", "transportation", "park", "place", "landcover", "boundary"}


class TileSource:
    def __init__(self, source: str = DEFAULT_SOURCE, cache_dir: Path | None = None, on_update=None):
        self.source = source
        self.cache_dir = cache_dir or CACHE_DIR
        self.on_update = on_update  # called when a tile arrives, to redraw
        self.template: str | None = None
        self.memory: dict[tuple[int, int, int], dict] = {}
        self.failed: dict[tuple[int, int, int], float] = {}
        self.inflight: set[tuple[int, int, int]] = set()
        self.status = "starting"  # starting | online | offline
        self._sem: asyncio.Semaphore | None = None

    def _disk(self, z, x, y) -> Path:
        return self.cache_dir / str(z) / str(x) / f"{y}.pbf"

    def get(self, z: int, x: int, y: int) -> dict | None:
        """A decoded tile if we have one; otherwise None, and fetch it in the background."""
        k = (z, x, y)
        if k in self.memory:
            return self.memory[k]
        path = self._disk(z, x, y)
        if path.exists():
            try:
                self.memory[k] = decode_tile(path.read_bytes(), LAYERS)
                return self.memory[k]
            except Exception:  # noqa: BLE001 - a corrupt cache file: drop it and refetch
                path.unlink(missing_ok=True)
        if k not in self.inflight and time.time() - self.failed.get(k, 0) > RETRY_AFTER:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                return None
            self.inflight.add(k)
            loop.create_task(self._fetch(k))
        return None

    async def _resolve_template(self) -> str:
        if self.template:
            return self.template
        if "{z}" in self.source:
            self.template = self.source
            return self.template
        meta = self.cache_dir / "tilejson.json"
        if meta.exists() and time.time() - meta.stat().st_mtime < TILEJSON_TTL:
            self.template = json.loads(meta.read_text())["tiles"][0]
            return self.template
        data = await asyncio.to_thread(_fetch, self.source)
        tj = json.loads(data)
        if not str(tj["tiles"][0]).startswith("https://"):
            raise ValueError("tile template must be https")
        meta.parent.mkdir(parents=True, exist_ok=True)
        meta.write_text(json.dumps(tj))
        self.template = tj["tiles"][0]
        return self.template

    async def _fetch(self, k: tuple[int, int, int]) -> None:
        if self._sem is None:
            self._sem = asyncio.Semaphore(4)
        z, x, y = k
        try:
            async with self._sem:
                template = await self._resolve_template()
                data = await asyncio.to_thread(_fetch, template.format(z=z, x=x, y=y))
            tile = await asyncio.to_thread(decode_tile, data, LAYERS)
            path = self._disk(z, x, y)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            self.memory[k] = tile
            self.status = "online"
            self.failed.pop(k, None)
            if self.on_update:
                self.on_update()
        except Exception:  # noqa: BLE001 - offline, blocked, rate-limited or bad data: leave the tile out
            self.failed[k] = time.time()
            self.status = "offline" if self.status != "online" else "online"
        finally:
            self.inflight.discard(k)

    def tiles_for(self, view: View) -> tuple[int, list[tuple[int, int, int, dict | None]]]:
        z = view.tile_zoom()
        while True:
            n = 2**z
            x0, y0, x1, y1 = view.bounds()
            tx0, tx1 = int(max(0, x0) * n), int(min(0.999999, x1) * n)
            ty0, ty1 = int(max(0, y0) * n), int(min(0.999999, y1) * n)
            if (tx1 - tx0 + 1) * (ty1 - ty0 + 1) <= 16 or z == 0:
                break
            z -= 1  # too many tiles for one screen: use coarser ones
        return z, [(z, tx, ty, self.get(z, tx, ty)) for tx in range(tx0, tx1 + 1) for ty in range(ty0, ty1 + 1)]


# ── drawing ───────────────────────────────────────────────────────────────────────────────────────────
BRAILLE = {(0, 0): 0x01, (0, 1): 0x02, (0, 2): 0x04, (1, 0): 0x08, (1, 1): 0x10, (1, 2): 0x20, (0, 3): 0x40, (1, 3): 0x80}
ROAD_CLASSES = {  # class -> (priority, minimum tile zoom to draw it)
    "motorway": (6, 0), "trunk": (5, 0), "primary": (4, 7), "secondary": (3, 10), "tertiary": (2, 11),
    "minor": (1, 13), "rail": (2, 11), "transit": (1, 12),
}
PLACE_RANK = {"city": 0, "town": 1, "suburb": 2, "village": 3, "quarter": 4, "neighbourhood": 5, "hamlet": 6}


class Canvas:
    def __init__(self, cols: int, rows: int):
        self.cols, self.rows = cols, rows
        self.dots = [[0] * cols for _ in range(rows)]
        self.fg = [[""] * cols for _ in range(rows)]
        self.prio = [[0] * cols for _ in range(rows)]
        self.bg = [[""] * cols for _ in range(rows)]
        self.labels: list[tuple[int, int, str, str]] = []  # (row, col, text, style)
        self._label_cells: set[tuple[int, int]] = set()

    def dot(self, x: int, y: int, style: str, prio: int) -> None:
        if 0 <= x < self.cols * 2 and 0 <= y < self.rows * 4:
            r, c = y // 4, x // 2
            self.dots[r][c] |= BRAILLE[(x % 2, y % 4)]
            if prio >= self.prio[r][c]:
                self.prio[r][c], self.fg[r][c] = prio, style

    def line(self, x0: float, y0: float, x1: float, y1: float, style: str, prio: int) -> None:
        w, h = self.cols * 2, self.rows * 4
        if (x0 < 0 and x1 < 0) or (y0 < 0 and y1 < 0) or (x0 >= w and x1 >= w) or (y0 >= h and y1 >= h):
            return
        steps = int(max(abs(x1 - x0), abs(y1 - y0))) + 1
        if steps > 4 * (w + h):  # absurdly long segment far off-screen: skip rather than walk it
            return
        for i in range(steps + 1):
            t = i / steps
            self.dot(int(x0 + (x1 - x0) * t), int(y0 + (y1 - y0) * t), style, prio)

    def fill(self, rings: list[list[tuple[float, float]]], style: str) -> None:
        """Even-odd scanline fill of cell backgrounds; rings are in cell coordinates."""
        edges = [(a, b) for ring in rings for a, b in zip(ring, ring[1:]) if a[1] != b[1]]
        if not edges:
            return
        lo = max(0, int(min(min(a[1], b[1]) for a, b in edges)))
        hi = min(self.rows - 1, int(max(max(a[1], b[1]) for a, b in edges)))
        for r in range(lo, hi + 1):
            yc = r + 0.5
            xs = sorted(a[0] + (yc - a[1]) * (b[0] - a[0]) / (b[1] - a[1])
                        for a, b in edges if (a[1] <= yc) != (b[1] <= yc))
            for x_in, x_out in zip(xs[0::2], xs[1::2]):
                for c in range(max(0, int(x_in + 0.5)), min(self.cols, int(x_out + 0.5))):
                    self.bg[r][c] = style

    def label(self, row: int, col: int, text: str, style: str) -> bool:
        from rich.cells import cell_len

        n = cell_len(text)
        if row < 0 or row >= self.rows or col < 0 or col + n > self.cols:
            return False
        if any((row, c) in self._label_cells for c in range(col - 1, col + n + 1)):
            return False
        self._label_cells.update((row, c) for c in range(col, col + n))
        self.labels.append((row, col, text, style))
        return True


def _on_tile_edge(p0, p1, extent: int) -> bool:
    """Polygons are clipped at tile borders; those clip edges aren't real shoreline."""
    return ((p0[0] <= 0 and p1[0] <= 0) or (p0[0] >= extent and p1[0] >= extent)
            or (p0[1] <= 0 and p1[1] <= 0) or (p0[1] >= extent and p1[1] >= extent))


def draw(view: View, tiles: list[tuple[int, int, int, dict | None]], colors: dict) -> Canvas:
    cv = Canvas(view.cols, view.rows)
    places = []
    for z, tx, ty, tile in tiles:
        if not tile:
            continue
        n = 2**z

        def pt(p, extent, tx=tx, ty=ty, n=n):
            return view.to_dot((tx + p[0] / extent) / n, (ty + p[1] / extent) / n)

        for layer, bg in (("park", colors["park"]), ("water", colors["water_bg"])):
            for f in tile.get(layer, []):
                if f["type"] != 3:
                    continue
                if layer == "park" and z < 10:
                    continue
                dot_rings = [[pt(p, f["extent"]) for p in part] for part in f["parts"]]
                cv.fill([[(x / 2, y / 4) for x, y in ring] for ring in dot_rings], bg)
                if layer == "water":  # trace the shoreline in dots: much finer than the cell fill
                    ext = f["extent"]
                    for part, ring in zip(f["parts"], dot_rings):
                        for (p0, a), (p1, b) in zip(zip(part, ring), zip(part[1:], ring[1:])):
                            if not _on_tile_edge(p0, p1, ext):
                                cv.line(*a, *b, colors["water"], 1)
        for f in tile.get("waterway", []):
            if f["props"].get("class") in ("river", "canal") or (z >= 12 and f["props"].get("class") == "stream"):
                for part in f["parts"]:
                    ps = [pt(p, f["extent"]) for p in part]
                    for a, b in zip(ps, ps[1:]):
                        cv.line(*a, *b, colors["water"], 3)
        for f in tile.get("transportation", []):
            cls = f["props"].get("class")
            if cls not in ROAD_CLASSES or f["type"] != 2:
                continue
            prio, min_z = ROAD_CLASSES[cls]
            if z < min_z:
                continue
            style = colors["road_major"] if prio >= 4 else (colors["rail"] if cls in ("rail", "transit") else colors["road_minor"])
            for part in f["parts"]:
                ps = [pt(p, f["extent"]) for p in part]
                for a, b in zip(ps, ps[1:]):
                    cv.line(*a, *b, style, prio)
        for f in tile.get("place", []):
            cls, name = f["props"].get("class"), f["props"].get("name:latin") or f["props"].get("name")
            if name and cls in PLACE_RANK and f["parts"] and (PLACE_RANK[cls] <= 2 or z >= 13):
                x, y = pt(f["parts"][0][0], f["extent"])
                from .util import clean

                places.append((PLACE_RANK[cls], f["props"].get("rank", 99) or 99, clean(name)[:40], x, y))
    w, h = view.dots
    seen = set()
    for _, _, name, x, y in sorted(places):  # tiles overlap, so the same place can come from several
        if name in seen or not (0 <= x < w and 0 <= y < h):
            continue
        seen.add(name)
        cv.label(int(y // 4), int(x // 2) - len(name) // 2, name, colors["label"])
    return cv
