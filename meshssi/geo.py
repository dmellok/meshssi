"""Distances, bearings and a text-mode map."""

import math

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


def render_map(me: tuple[float, float, str] | None, nodes: list[dict], width: int, height: int,
               palette_fn, styles: dict) -> Text:
    """Plot nodes (dicts with name, type, lat, lon) on a width×height character grid around `me`.

    Uses an equirectangular projection with terminal cells treated as ~2:1 (tall), which is plenty
    for the few-km to few-hundred-km spans of a mesh.
    """
    pts = [n for n in nodes if has_fix(n.get("lat"), n.get("lon"))]
    if me and has_fix(me[0], me[1]):
        pts = [{"name": me[2], "type": 0, "lat": me[0], "lon": me[1], "me": True}] + pts
    out = Text()
    if len(pts) < 1 or width < 20 or height < 6:
        out.append("No nodes with a location yet. Adverts that carry GPS positions will appear here.", styles["dim"])
        return out
    lat0 = sum(p["lat"] for p in pts) / len(pts)
    kx = math.cos(math.radians(lat0))
    xs = [p["lon"] * kx for p in pts]
    ys = [p["lat"] for p in pts]
    minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
    # one terminal row is about two columns tall, hence the /2 on latitude below
    spanx = max(maxx - minx, 1e-4)
    spany = max(maxy - miny, 1e-4)
    plot_w, plot_h = width - 2, height - 3
    scale = min(plot_w / spanx, plot_h * 2 / spany) if spanx and spany else 1
    cx, cy = (minx + maxx) / 2, (miny + maxy) / 2
    grid = [[" "] * width for _ in range(height - 1)]
    style = [[""] * width for _ in range(height - 1)]

    def fits(row, col, n):
        return 0 <= row < len(grid) and col >= 0 and col + n <= width and all(grid[row][c] == " " for c in range(col, col + n))

    def put(row, col, s, st):
        for i, ch in enumerate(s):
            grid[row][col + i] = ch
            style[row][col + i] = st

    placed = []
    for p in sorted(pts, key=lambda p: not p.get("me")):
        col = int(round((p["lon"] * kx - cx) * scale + width / 2))
        row = int(round(-(p["lat"] - cy) * scale / 2 + (height - 1) / 2))
        col = max(0, min(width - 1, col))
        row = max(0, min(height - 2, row))
        st = "bold reverse" if p.get("me") else palette_fn(p["name"])
        if grid[row][col] == " ":
            grid[row][col] = GLYPH.get(p["type"], "?")
            style[row][col] = st
        placed.append((row, col, p, st))
    for row, col, p, st in placed:  # labels after markers so markers win; a label goes right, else left, else nowhere
        label = " " + p["name"][:18] + " "
        for start in (col + 1, col - len(label)):
            if fits(row, start, len(label)):
                put(row, start, label, st if not p.get("me") else "bold")
                break
    for r in range(len(grid)):
        c = 0
        while c < width:  # append runs of same-styled cells, not single characters
            end = c
            while end < width and style[r][end] == style[r][c]:
                end += 1
            out.append("".join(grid[r][c:end]), style[r][c] or None)
            c = end
        out.append("\n")
    widest = max(distance_km(ys[0], xs[0] / kx, y, x / kx) for x, y in zip(xs, ys)) if pts else 0
    out.append(f"◉ you  R repeater  @ chat  # room  S sensor   ·   {len(pts)} located   ·   farthest {fmt_distance(widest)}",
               styles["dim"])
    return out
