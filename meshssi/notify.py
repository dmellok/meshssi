"""Desktop notifications (macOS via osascript, Linux via notify-send)."""

import asyncio
import json
import shutil
import sys


async def desktop_notify(title: str, body: str) -> None:
    if sys.platform == "darwin":
        script = f"display notification {json.dumps(body)} with title {json.dumps(title)}"
        cmd = ["osascript", "-e", script]
    elif shutil.which("notify-send"):
        cmd = ["notify-send", "--app-name=meshssi", title, body]
    else:
        return
    try:
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        await asyncio.wait_for(proc.wait(), 5)
    except (OSError, asyncio.TimeoutError):
        pass
