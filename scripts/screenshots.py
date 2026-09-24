"""Render the README screenshots from the simulated mesh (no radio needed).

    .venv/bin/python scripts/screenshots.py      # writes docs/*.png (needs Chrome, and network for map tiles)

Text is set in JetBrains Mono (downloaded once into ~/.cache/meshssi/fonts).
"""

import asyncio
import base64
import random
import re
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from meshssi.demo import BY_NAME, DemoApp  # noqa: E402

DOCS = ROOT / "docs"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
FONT_DIR = Path.home() / ".cache" / "meshssi" / "fonts"
FONT_URL = "https://github.com/JetBrains/JetBrainsMono/raw/master/fonts/webfonts/JetBrainsMono-{}.woff2"
THEME = "rose-pine"
COLS, ROWS = 124, 36
TMP = Path(tempfile.mkdtemp(prefix="meshssi-shots-"))


async def wait_for_map(app, pilot):
    for _ in range(100):
        await pilot.pause(0.25)
        ms = getattr(app, "map_state", None)
        if ms and ms.cache and ms.cache[1] is not None and not ms.rendering and not ms.tiles.inflight:
            return


async def main() -> list[str]:
    random.seed(7)
    app = DemoApp(live=False)
    shots = []
    async with app.run_test(size=(COLS, ROWS)) as pilot:
        for _ in range(60):
            await pilot.pause(0.1)
            if app.connected and app.find_window("chan:Public") and app.find_window("chan:Public").recs:
                break
        app.theme_name = THEME
        app.apply_theme()
        app.query_one("#nicklist").styles.width = 28

        async def shot(name, pause=0.5):
            app.redraw()
            app.refresh_chrome()
            await pilot.pause(pause)
            app.save_screenshot(f"{name}.svg", str(TMP))
            shots.append(name)

        def goto(key):
            app.switch(app.windows.index(app.find_window(key)))

        inp = app.query_one("#input")
        ada = app.find_window("dm:" + BY_NAME["ada 🦊"]["public_key"])

        # the classic layout: channel with a DM split above it
        goto("chan:Public")
        app.split_win = ada
        await shot("channel")
        app.split_win = None

        # composing: a long message wraps, the counter shows it'll go as two packets
        inp.value = ("heading up to the ridge this arvo with the new T-Deck and a 5dBi whip, will post traces "
                     "from the lookout and the summit if anyone wants to come along, bring snacks")
        await shot("compose")
        inp.value = ""

        goto("dm:" + BY_NAME["ada 🦊"]["public_key"])
        await shot("dm")

        # click a hop count: the route by name, then a trace along it
        goto("chan:Public")
        pub = app.find_window("chan:Public")
        rec = next(r for r in pub.recs if r.get("hops", 0) >= 2 and r.get("route"))
        await app.action_hops(rec["id"])
        await pilot.pause(3)
        await shot("trace")

        goto("dm:" + BY_NAME["Ridgeline Rpt"]["public_key"])
        await app.run_command("rstatus Ridgeline Rpt")
        await app.run_command("neighbours Ridgeline Rpt")
        await pilot.pause(1.5)
        await shot("repeater")

        await app.run_command("rf")
        await shot("rf")
        await app.run_command("map")
        await wait_for_map(app, pilot)
        await shot("map")
        await app.run_command("graphs")
        await shot("graphs")
        await app.run_command("dash")
        await shot("dash")

        # the friendlier layer
        await app.run_command("layout easy")
        goto("chan:Public")
        await pilot.pause(0.5)
        await shot("easy")
        app.open_card(key=BY_NAME["Ridgeline Rpt"]["public_key"])
        await pilot.pause(0.5)
        app.save_screenshot("card.svg", str(TMP))
        shots.append("card")
        app.screen.dismiss()
        await pilot.pause(0.3)
        inp.value = "/whois "
        await shot("hints")
        inp.value = ""
        app.action_command_palette()
        await pilot.pause(0.6)
        await pilot.press(*"trace")
        await pilot.pause(0.6)
        app.save_screenshot("palette.svg", str(TMP))
        shots.append("palette")
        await app.action_quit()
    return shots


def font_css() -> str:
    FONT_DIR.mkdir(parents=True, exist_ok=True)
    css = ""
    for name, weight in (("Regular", 400), ("Bold", 700)):
        path = FONT_DIR / f"JetBrainsMono-{name}.woff2"
        if not path.exists():
            urllib.request.urlretrieve(FONT_URL.format(name), path)
        data = base64.b64encode(path.read_bytes()).decode()
        css += f'@font-face{{font-family:"JetBrains Mono";font-weight:{weight};src:url(data:font/woff2;base64,{data}) format("woff2")}}'
    return css


def to_png(name: str, css: str) -> None:
    svg = (TMP / f"{name}.svg").read_text()
    w, h = map(float, re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"', svg).groups())
    svg = re.sub(r"@font-face \{.*?\}", "", svg, flags=re.S)
    svg = svg.replace("font-family: Fira Code, monospace;", 'font-family: "JetBrains Mono", monospace;')
    svg = svg.replace("font-family: arial;", 'font-family: "JetBrains Mono", monospace;')
    svg = svg.replace("<svg ", f'<svg width="{w}" ', 1)
    page = TMP / f"{name}.html"
    page.write_text(f"<html><head><meta charset='utf-8'><style>{css} body{{margin:0;background:#15151c}}</style></head>"
                    f"<body>{svg}</body></html>")
    subprocess.run([CHROME, "--headless", "--hide-scrollbars", "--force-device-scale-factor=2", "--virtual-time-budget=4000",
                    f"--window-size={int(w)},{int(h)}", f"--screenshot={DOCS / (name + '.png')}", page.as_uri()],
                   check=True, capture_output=True)


if __name__ == "__main__":
    DOCS.mkdir(exist_ok=True)
    names = asyncio.run(main())
    css = font_css()
    for n in names:
        to_png(n, css)
        print("wrote", DOCS / f"{n}.png")
    for stale in ("device.png",):
        (DOCS / stale).unlink(missing_ok=True)
