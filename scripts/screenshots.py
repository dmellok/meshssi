"""Render README screenshots from the simulated mesh (no radio needed).

    .venv/bin/python scripts/screenshots.py      # writes docs/*.png via headless Chrome
"""

import asyncio
import random
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from meshssi.demo import BY_NAME, DemoApp  # noqa: E402

DOCS = ROOT / "docs"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


async def main() -> list[str]:
    random.seed(7)
    DOCS.mkdir(exist_ok=True)
    app = DemoApp(live=False)
    shots = []
    async with app.run_test(size=(132, 36)) as pilot:
        for _ in range(50):
            await pilot.pause(0.1)
            if app.connected and app.find_window("chan:Public") and app.find_window("chan:Public").recs:
                break
        await pilot.pause(0.5)

        async def shot(name):
            await pilot.pause(0.4)
            app.save_screenshot(f"{name}.svg", str(DOCS))
            shots.append(name)

        def goto(key):
            app.switch(app.windows.index(app.find_window(key)))

        goto("chan:Public")
        await shot("channel")
        goto("dm:" + BY_NAME["ada 🦊"]["public_key"])
        await shot("dm")
        goto("dm:" + BY_NAME["Ridgeline Rpt"]["public_key"])
        await app.run_command("rstatus Ridgeline Rpt")
        await shot("repeater")
        app.switch(0)
        await app.run_command("info")
        await app.run_command("stats")
        await shot("device")
        await app.run_command("rf")
        await shot("rf")
        await app.run_command("map")
        await shot("map")
        await app.run_command("graphs")
        await shot("graphs")
        await app.run_command("dash")
        await shot("dash")
    return shots


def to_png(name: str) -> None:
    svg = DOCS / f"{name}.svg"
    w, h = map(float, re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"', svg.read_text()).groups())
    # an HTML wrapper with zero margin so the capture is exactly the SVG
    html = DOCS / f"_{name}.html"
    html.write_text(f'<html><body style="margin:0;background:#000"><img src="{svg.name}" width="{w}" height="{h}"></body></html>')
    subprocess.run([CHROME, "--headless", "--hide-scrollbars", "--force-device-scale-factor=2",
                    f"--window-size={int(w)},{int(h)}", f"--screenshot={DOCS / (name + '.png')}", html.as_uri()],
                   check=True, capture_output=True)
    html.unlink()
    svg.unlink()


if __name__ == "__main__":
    for n in asyncio.run(main()):
        to_png(n)
        print("wrote", DOCS / f"{n}.png")
