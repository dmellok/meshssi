"""Distances, bearings and a text-mode map."""

import math

from rich.cells import cell_len
from rich.text import Text

EARTH_KM = 6371.0
COMPASS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
GLYPH = {0: "◉", 1: "@", 2: "R", 3: "#", 4: "S"}


def has_fix(lat, lon) -> bool:
    return bool(lat or lon) and lat is not None and lon is not None


def distance_km(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_KM * math.asin(math.sqrt(a))


def bearing(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    x = math.sin(dl) * math.cos(p2)
    y = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def compass(deg: float) -> str:
    return COMPASS[int((deg + 11.25) // 22.5) % 16]


def fmt_distance(km: float) -> str:
    return f"{km * 1000:.0f} m" if km < 1 else f"{km:.1f} km"


def describe(me: tuple[float, float] | None, lat, lon) -> str:
    if not me or not has_fix(*me) or not has_fix(lat, lon):
        return ""
    return f"{fmt_distance(distance_km(*me, lat, lon))} {compass(bearing(*me, lat, lon))}"


def local_points(me: tuple[float, float] | None, nodes: list[dict]) -> tuple[list[dict], int]:
    """Located nodes, minus far-off outliers (the odd 500 km skip) so the local mesh fills the map."""
    pts = [n for n in nodes if has_fix(n.get("lat"), n.get("lon"))]
    if len(pts) <= 4:
        return pts, 0
    if me and has_fix(*me):
        c_lat, c_lon = me
    else:
        c_lat, c_lon = sorted(p["lat"] for p in pts)[len(pts) // 2], sorted(p["lon"] for p in pts)[len(pts) // 2]
    dists = sorted(distance_km(c_lat, c_lon, p["lat"], p["lon"]) for p in pts)
    limit = max(5.0, 3 * dists[int(len(dists) * 0.8)])
    kept = [p for p in pts if distance_km(c_lat, c_lon, p["lat"], p["lon"]) <= limit]
    return kept, len(pts) - len(kept)


def render_map(view, me: tuple[float, float, str] | None, nodes: list[dict], palette_fn, styles: dict,
               canvas=None, dot_style: str = "braille") -> tuple[Text, int]:
    """Draw nodes over an optional basemap canvas. Returns the map text and how many nodes are off-screen."""
    from .basemap import to_world

    cols, rows = view.cols, view.rows
    char = [[" "] * cols for _ in range(rows)]
    fg = [[""] * cols for _ in range(rows)]
    bg = [[""] * cols for _ in range(rows)]
    if canvas is not None:
        for r in range(rows):
            for c in range(cols):
                d = canvas.dots[r][c]
                if d:
                    char[r][c] = chr(0x2800 + d) if dot_style == "braille" else "·"
                    fg[r][c] = canvas.fg[r][c]
                bg[r][c] = canvas.bg[r][c]
    taken: set[tuple[int, int]] = set()  # cells used by nodes and their labels

    def free(row, col, n):
        return 0 <= row < rows and col >= 0 and col + n <= cols and all((row, c) not in taken for c in range(col, col + n))

    def put(row, col, s, st):
        c = col
        for ch in s:  # wide characters (emoji) take two cells: the second is an empty placeholder
            char[row][c], fg[row][c] = ch, st
            taken.add((row, c))
            if cell_len(ch) == 2 and c + 1 < cols:
                char[row][c + 1], fg[row][c + 1] = "", st
                taken.add((row, c + 1))
                c += 1
            c += 1

    pts = [n for n in nodes if has_fix(n.get("lat"), n.get("lon"))]
    if me and has_fix(me[0], me[1]):
        pts = [{"name": me[2], "type": 0, "lat": me[0], "lon": me[1], "me": True}] + pts
    placed, off = [], 0
    for p in sorted(pts, key=lambda p: not p.get("me")):
        col, row = view.to_cell(*to_world(p["lat"], p["lon"]))
        if not (0 <= col < cols and 0 <= row < rows):
            off += 0 if p.get("me") else 1
            continue
        st = "bold reverse" if p.get("me") else f"bold {palette_fn(p['name'])}"
        if (row, col) not in taken:
            put(row, col, GLYPH.get(p["type"], "?"), st)
        placed.append((row, col, p, st))
    for row, col, p, st in placed:  # labels after markers so markers win; a label goes right, else left, else nowhere
        label = " " + p["name"][:24] + " "
        for start in (col + 1, col - cell_len(label)):
            if free(row, start, cell_len(label)):
                put(row, start, label, st if not p.get("me") else "bold")
                break
    if canvas is not None:  # place names from the basemap, where they don't collide with nodes
        for row, col, text, st in canvas.labels:
            if free(row, col, cell_len(text)):
                put(row, col, text, st)
    out = Text()
    for r in range(rows):
        c = 0
        while c < cols:  # append runs of same-styled cells, not single characters
            end = c
            while end < cols and fg[r][end] == fg[r][c] and bg[r][end] == bg[r][c]:
                end += 1
            style = " ".join(x for x in (fg[r][c], f"on {bg[r][c]}" if bg[r][c] else "") if x) or None
            out.append("".join(char[r][c:end]), style)
            c = end
        out.append("\n")
    return out, off
