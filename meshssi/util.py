"""Small formatting helpers shared across the app."""

import re
import time
import zlib

from rich.text import Text

URL_RE = re.compile(r"(https?://[^\s<>\"']+|www\.[^\s<>\"']+)")
SPARKS = "▁▂▃▄▅▆▇█"


def ago(ts: float | int | None) -> str:
    if not ts:
        return "never"
    d = int(time.time() - ts)
    if d < 0:
        return "future?"
    for unit, secs in (("d", 86400), ("h", 3600), ("m", 60)):
        if d >= secs:
            return f"{d // secs}{unit} ago"
    return f"{d}s ago"


def fmt_duration(secs: int) -> str:
    d, rem = divmod(int(secs), 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    return (f"{d}d " if d else "") + f"{h:02d}:{m:02d}:{s:02d}"


def split_utf8(text: str, limit: int) -> list[str]:
    """Split text into chunks of at most `limit` UTF-8 bytes, preferring word boundaries."""
    chunks: list[str] = []
    while len(text.encode()) > limit:
        cut = len(text)
        while len(text[:cut].encode()) > limit:
            cut -= 1
        space = text.rfind(" ", 0, cut)
        if space > cut // 2:
            cut = space
        chunks.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    if text:
        chunks.append(text)
    return chunks


def pick_color(name: str, palette: list[str]) -> str:
    return palette[zlib.crc32(name.encode()) % len(palette)]


def sparkline(values: list[float], width: int) -> str:
    vals = [v for v in values if v is not None][-width:]
    if not vals:
        return ""
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1
    return "".join(SPARKS[min(7, int((v - lo) / span * 7.999))] for v in vals)


def linkify(text: Text) -> Text:
    """Make URLs clickable in terminals that support OSC 8 hyperlinks."""
    plain = text.plain
    for m in URL_RE.finditer(plain):
        url = m.group(0).rstrip(".,);:!?")
        href = url if url.startswith("http") else "https://" + url
        text.stylize(f"underline link {href}", m.start(), m.start() + len(url))
    return text


_SHORTCODE_RE = re.compile(r":([a-z0-9_+\-]+):")


def expand_shortcodes(text: str) -> str:
    """:thumbs_up: -> 👍, using Rich's emoji table; unknown codes are left alone."""
    from rich._emoji_codes import EMOJI

    return _SHORTCODE_RE.sub(lambda m: EMOJI.get(m.group(1), EMOJI.get(m.group(1).replace("-", "_"), m.group(0))), text)
