"""Small formatting helpers shared across the app."""

import re
import time
import zlib

from rich.text import Text

URL_RE = re.compile(r"(https?://[^\s<>\"'\x00-\x1f\x7f-\x9f]+|www\.[^\s<>\"'\x00-\x1f\x7f-\x9f]+)")
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
    """Split text into chunks of at most `limit` UTF-8 bytes, preferring word boundaries and never cutting
    through an emoji sequence (ZWJ joins, variation selectors, skin tones). Empty chunks are never returned."""
    limit = max(limit, 8)
    joiners = ("\u200d", "\ufe0f", "\ufe0e") + tuple(chr(c) for c in range(0x1F3FB, 0x1F400))
    chunks: list[str] = []
    text = text.strip()
    while len(text.encode()) > limit:
        cut = len(text)
        while cut > 1 and len(text[:cut].encode()) > limit:
            cut -= 1
        # don't split inside an emoji sequence: back off to before its first character
        while 1 < cut < len(text) and (text[cut] in joiners or text[cut - 1] == "\u200d"):
            cut -= 1
        space = text.rfind(" ", 0, cut)
        if space > cut // 2:
            cut = space
        chunk = text[:cut].rstrip()
        if chunk:
            chunks.append(chunk)
        text = text[max(cut, 1):].lstrip()
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
        url = m.group(0).rstrip(".,;:!?")
        while url.endswith(")") and url.count(")") > url.count("("):  # keep ")" that closes a "(" in the URL
            url = url[:-1]
        href = url if url.startswith("http") else "https://" + url
        text.stylize(f"underline link {href}", m.start(), m.start() + len(url))
    return text


_SHORTCODE_RE = re.compile(r":([a-z0-9_+\-]+):")


def expand_shortcodes(text: str) -> str:
    """:thumbs_up: -> 👍, using Rich's emoji table; unknown codes are left alone."""
    from rich._emoji_codes import EMOJI

    return _SHORTCODE_RE.sub(lambda m: EMOJI.get(m.group(1), EMOJI.get(m.group(1).replace("-", "_"), m.group(0))), text)


_EMOJI_NAMES: dict[str, str] | None = None


def _emoji_names() -> dict[str, str]:
    """emoji character(s) -> a readable name, e.g. 🐢 -> turtle (built lazily from Rich's table)."""
    global _EMOJI_NAMES
    if _EMOJI_NAMES is None:
        from rich._emoji_codes import EMOJI

        names: dict[str, str] = {}
        for name, char in EMOJI.items():
            char = char.replace("️", "")
            best = names.get(char)
            # prefer real words over things like "+1", then the shortest
            if best is None or (name[0].isalpha(), -len(name)) > (best[0].isalpha(), -len(best)):
                names[char] = name
        _EMOJI_NAMES = names
    return _EMOJI_NAMES


def _split_emoji(text: str) -> list[tuple[str, str | None]]:
    """Tokenise into (chunk, emoji_name or None), matching the longest emoji sequence at each point."""
    table = _emoji_names()
    text = text.replace("️", "")
    out, i = [], 0
    while i < len(text):
        for size in range(min(8, len(text) - i), 0, -1):
            chunk = text[i : i + size]
            if not chunk.isascii() and chunk in table:
                out.append((chunk, table[chunk]))
                i += size
                break
        else:
            out.append((text[i], None))
            i += 1
    return out


def name_forms(name: str) -> set[str]:
    """Lowercase spellings a person might type for a node name.

    "Turtle Hops 🐢" -> {"turtle hops 🐢", "turtle hops", "turtle hops turtle"}; an emoji-only
    name like "🦊" -> {"🦊", "fox_face", "fox face"}.
    """
    parts = _split_emoji(name)
    stripped = " ".join("".join(c for c, e in parts if e is None).split())
    named = " ".join("".join(f" {e} " if e else c for c, e in parts).split())
    forms = {name.lower(), stripped.lower(), named.lower(), named.replace("_", " ").lower()}
    return {f for f in forms if f}


def name_matches(name: str, typed: str) -> bool:
    """Does `typed` (a fragment, maybe :shortcode-ish) start the name or any word in it, ignoring emoji?"""
    frag = " ".join(typed.lower().strip(":").split())
    if not frag:
        return True
    for form in name_forms(name):
        if form.startswith(frag):
            return True
        words = form.split(" ")
        if any(" ".join(words[i:]).startswith(frag) for i in range(1, len(words))):
            return True
    return False


_CONTROL = {c: None for c in list(range(0x00, 0x20)) + [0x7F] + list(range(0x80, 0xA0))}
_CONTROL.update({0x09: " ", 0x0A: " ", 0x0D: " "})


# Characters whose on-screen width terminals and Rich disagree about. Left in, they shift the rest of the
# row and leave stale cells behind (a misaligned sidebar, stray blocks). Dropped for display:
_INVISIBLE = {c: None for c in (
    [0x00AD, 0x034F, 0x115F, 0x1160, 0x17B4, 0x17B5, 0x180E, 0x200B, 0x200C, 0x200D, 0x200E, 0x200F, 0x2060,
     0x2061, 0x2062, 0x2063, 0x2064, 0x3164, 0xFE0E, 0xFE0F, 0xFEFF, 0xFFA0, 0x20E3]  # joiners, selectors, fillers
    + list(range(0x202A, 0x202F)) + list(range(0x2066, 0x206A))  # bidi controls
    + list(range(0x1F3FB, 0x1F400))  # skin-tone modifiers
    + list(range(0xE0000, 0xE0080))  # tag characters (subdivision flags)
)}
_INVISIBLE.update({c: chr(c - 0x1F1E6 + ord("A")) for c in range(0x1F1E6, 0x1F200)})  # flag letters -> "AU"


def clean(text) -> str:
    """Make text from the mesh safe and predictable to display: drop terminal control characters (ESC, OSC,
    C1...), turn line breaks into spaces, and drop the invisible joiners/selectors that make emoji render at a
    different width than they're measured (flags become their two letters, 👍🏽 becomes 👍)."""
    return str(text).translate(_CONTROL).translate(_INVISIBLE) if text is not None else ""


def write_private(path, text: str) -> None:
    """Create a new file readable only by you. Refuses to overwrite (FileExistsError) or follow a symlink."""
    import os

    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
