"""Render docs/themes.png: the demo's channel window in every theme, in a grid.

    .venv/bin/python scripts/theme_gallery.py      # needs Chrome
"""

import asyncio
import random
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from meshssi.demo import DemoApp  # noqa: E402
from meshssi.themes import THEMES  # noqa: E402
from screenshots import font_css  # noqa: E402

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
COLS, TILE_W = 4, 520


async def render(out: Path) -> list[str]:
    random.seed(7)
    app = DemoApp(live=False)
    async with app.run_test(size=(100, 24)) as pilot:
        for _ in range(50):
            await pilot.pause(0.1)
            if app.connected and app.find_window("chan:Public") and app.find_window("chan:Public").recs:
                break
        app.switch(app.windows.index(app.find_window("chan:Public")))
        app.query_one("#nicklist").display = False
        await pilot.pause(0.5)
        for name in THEMES:
            app.theme_name = name
            app.title = f"meshssi · {name}"
            app.apply_theme()
            app.redraw()
            app.refresh_chrome()
            await pilot.pause(0.3)
            app.save_screenshot(f"{name}.svg", str(out))
    return list(THEMES)


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="meshssi-themes-"))
    names = asyncio.run(render(tmp))
    import re

    def styled(n):
        svg = (tmp / f"{n}.svg").read_text()
        svg = re.sub(r"@font-face \{.*?\}", "", svg, flags=re.S)
        svg = svg.replace("font-family: Fira Code, monospace;", 'font-family: "JetBrains Mono", monospace;')
        svg = svg.replace("font-family: arial;", 'font-family: "JetBrains Mono", monospace;')
        return svg.replace("<svg ", f'<svg width="{TILE_W}" ', 1)

    tiles = "".join(f"<div>{styled(n)}</div>" for n in names)
    rows = -(-len(names) // COLS)
    width = COLS * TILE_W + (COLS + 1) * 16
    (tmp / "grid.html").write_text(
        f"<html><head><meta charset='utf-8'><style>{font_css()} body{{margin:0;background:#16161a}}</style></head><body>"
        f"<div style='display:grid;grid-template-columns:repeat({COLS},{TILE_W}px);gap:16px;padding:16px'>{tiles}</div></body></html>")
    height = 16 + rows * (int(TILE_W * 0.52) + 16)
    target = ROOT / "docs" / "themes.png"
    subprocess.run([CHROME, "--headless", "--hide-scrollbars", "--force-device-scale-factor=1.5", f"--window-size={width},{height}",
                    "--virtual-time-budget=4000", f"--screenshot={target}", (tmp / "grid.html").as_uri()], check=True, capture_output=True)
    print("wrote", target)


if __name__ == "__main__":
    main()
