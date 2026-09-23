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

from meshssi.demo import DemoApp  # noqa: E402
from meshssi.themes import THEMES  # noqa: E402

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
COLS, TILE_W = 4, 520


async def render(out: Path) -> list[str]:
    random.seed(7)
    app = DemoApp(live=False)
    async with app.run_test(size=(104, 24)) as pilot:
        for _ in range(50):
            await pilot.pause(0.1)
            if app.connected and app.find_window("chan:Public") and app.find_window("chan:Public").recs:
                break
        app.switch(app.windows.index(app.find_window("chan:Public")))
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
    tiles = "".join(f'<div><img src="{n}.svg" width="{TILE_W}"></div>' for n in names)
    rows = -(-len(names) // COLS)
    width = COLS * TILE_W + (COLS + 1) * 16
    (tmp / "grid.html").write_text(
        f"<html><body style='margin:0;background:#16161a'><div style='display:grid;grid-template-columns:repeat({COLS},{TILE_W}px);"
        f"gap:16px;padding:16px'>{tiles}</div></body></html>")
    height = 16 + rows * (int(TILE_W * 0.52) + 16)
    target = ROOT / "docs" / "themes.png"
    subprocess.run([CHROME, "--headless", "--hide-scrollbars", "--force-device-scale-factor=1.5", f"--window-size={width},{height}",
                    f"--screenshot={target}", (tmp / "grid.html").as_uri()], check=True, capture_output=True)
    print("wrote", target)


if __name__ == "__main__":
    main()
