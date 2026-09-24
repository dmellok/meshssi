"""Desktop notifications (macOS via osascript, Linux via notify-send)."""

import asyncio
import shutil
import sys

from .util import clean


async def desktop_notify(title: str, body: str) -> None:
    title, body = clean(title)[:120], clean(body)[:400]
    if sys.platform == "darwin":  # passed as arguments, never spliced into the script
        cmd = ["osascript", "-e", "on run argv", "-e",
               "display notification (item 2 of argv) with title (item 1 of argv)", "-e", "end run", title, body]
    elif shutil.which("notify-send"):
        escape = str.maketrans({"&": "&amp;", "<": "&lt;", ">": "&gt;"})  # many daemons render markup
        cmd = ["notify-send", "--app-name=meshssi", "--", title.translate(escape), body.translate(escape)]
    else:
        return
    try:
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        await asyncio.wait_for(proc.wait(), 5)
    except (OSError, asyncio.TimeoutError):
        pass
