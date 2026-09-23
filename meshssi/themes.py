"""Colour themes. Each maps UI roles to Rich/Textual colours.

Most themes are built from a palette with `palette()`, which assigns every role; hand-written ones are
completed with `complete()` so newer roles (graphs, packet types, signal quality) always exist.
"""

from textual.color import Color

ROLES = ("background", "foreground", "bar_bg", "bar_fg", "bracket", "timestamp", "own_nick", "hilight", "hilight_text",
         "notice", "error", "ok", "dim", "meta", "sidebar_bg", "sidebar_border", "act", "unread", "nicks")


def palette(*, bg, fg, bar_bg, bar_fg, dim, muted, red, green, yellow, blue, magenta, cyan, orange,
            bright=None, sidebar_bg=None, border=None, extra=(), **overrides) -> dict:
    """Build a full theme from a terminal-style palette."""
    bright = bright or fg
    t = {
        "background": bg, "foreground": fg, "bar_bg": bar_bg, "bar_fg": bar_fg,
        "bracket": blue, "timestamp": dim, "own_nick": f"bold {bright}",
        "hilight": f"bold {bg} on {magenta}", "hilight_text": f"bold {magenta}",
        "notice": f"bold {blue}", "error": f"bold {red}", "ok": f"bold {green}",
        "dim": dim, "meta": muted, "sidebar_bg": sidebar_bg or bg, "sidebar_border": border or muted,
        "act": [dim, f"bold {bright}", f"bold {magenta}"], "unread": f"bold {yellow}",
        "nicks": [cyan, green, yellow, magenta, blue, orange, red, *extra],
        "blue": blue, "green": green,
    }
    t.update(overrides)
    return complete(t)


def complete(t: dict) -> dict:
    """Fill roles added after a theme was written, derived from its nick palette."""
    n = t["nicks"]
    t.setdefault("good", t["ok"].replace("bold ", ""))
    t.setdefault("warn", t["unread"].replace("bold ", ""))
    t.setdefault("bad", t["error"].replace("bold ", ""))
    t.setdefault("status_ok", t["good"])
    t.setdefault("status_bad", t["error"])
    t.setdefault("graph", [n[i % len(n)] for i in range(8)])
    bg, fg = t["background"], t["foreground"]
    blue, green = t.get("blue", "#4f7fcf"), t.get("green", "#5f9f5f")
    t.setdefault("map", {  # basemap colours, blended into the background so nodes stay the focus
        "water_bg": blend(bg, blue, 0.30), "water": blend(bg, blue, 0.75), "park": blend(bg, green, 0.14),
        "road_major": blend(bg, fg, 0.55), "road_minor": blend(bg, fg, 0.30), "rail": blend(bg, fg, 0.22),
        "label": f"italic {blend(bg, fg, 0.62)}",
    })
    t.setdefault("ptype", {
        "ADVERT": t["ok"], "GRP_TXT": n[0], "GRP_DATA": n[0], "TXT_MSG": n[3 % len(n)], "PATH": n[2 % len(n)],
        "TRACE": f"bold {n[2 % len(n)]}", "REQ": n[4 % len(n)], "RESPONSE": n[4 % len(n)], "ANON_REQ": n[4 % len(n)],
        "CONTROL": n[5 % len(n)], "ACK": t["dim"], "MULTIPART": t["dim"], "RAW_CUSTOM": t["dim"],
    })
    return t


def blend(a: str, b: str, t: float) -> str:
    return Color.parse(a).blend(Color.parse(b), t).hex


THEMES: dict[str, dict] = {
    # ── the originals ─────────────────────────────────────────────────────
    "irssi": complete({
        "background": "#000000", "foreground": "#d0d0d0", "bar_bg": "#00005f", "bar_fg": "#ffffff",
        "bracket": "#5f87ff", "timestamp": "#808080", "own_nick": "bold white", "hilight": "bold black on magenta",
        "hilight_text": "bold magenta", "notice": "bold blue", "error": "bold red", "ok": "bold green",
        "dim": "#6c6c6c", "meta": "#595959", "sidebar_bg": "#0a0a0a", "sidebar_border": "#303030",
        "act": ["#9e9e9e", "bold white", "bold magenta"], "unread": "bold yellow",
        "nicks": ["cyan", "green", "yellow", "magenta", "bright_blue", "bright_cyan", "bright_green",
                  "bright_yellow", "bright_magenta", "orange1", "orchid", "spring_green2", "deep_sky_blue1",
                  "light_salmon1", "khaki1", "plum1"],
        "graph": ["cyan", "green", "yellow", "magenta", "bright_blue", "orange1", "orchid", "green"],
        "blue": "#5f87ff", "green": "#5faf5f",
    }),
    "midnight": palette(
        bg="#0b1020", fg="#c8d3f5", bar_bg="#1e2a4a", bar_fg="#c8d3f5", dim="#5b6a95", muted="#444a73",
        red="#ff757f", green="#c3e88d", yellow="#ffc777", blue="#82aaff", magenta="#c099ff", cyan="#86e1fc",
        orange="#ff966c", bright="#ffffff", sidebar_bg="#0f1528", border="#2f3b63",
        extra=("#4fd6be", "#fca7ea", "#b4f9f8", "#f8bd96"), hilight="bold #0b1020 on #ff966c", hilight_text="bold #ff966c"),
    "gruvbox": palette(
        bg="#1d2021", fg="#ebdbb2", bar_bg="#3c3836", bar_fg="#ebdbb2", dim="#928374", muted="#665c54",
        red="#fb4934", green="#b8bb26", yellow="#fabd2f", blue="#83a598", magenta="#d3869b", cyan="#8ec07c",
        orange="#fe8019", bright="#fbf1c7", sidebar_bg="#232627", border="#504945",
        extra=("#458588", "#98971a", "#d79921", "#b16286", "#689d6a", "#d65d0e"),
        bracket="#d79921", hilight="bold #1d2021 on #fb4934", hilight_text="bold #fb4934"),
    "mono": complete({
        "background": "#000000", "foreground": "#c0c0c0", "bar_bg": "#303030", "bar_fg": "#ffffff",
        "bracket": "#808080", "timestamp": "#707070", "own_nick": "bold #ffffff", "hilight": "bold reverse",
        "hilight_text": "bold #ffffff", "notice": "bold #a0a0a0", "error": "bold #ffffff", "ok": "#e0e0e0",
        "dim": "#606060", "meta": "#505050", "sidebar_bg": "#0a0a0a", "sidebar_border": "#303030",
        "act": ["#808080", "bold #e0e0e0", "bold reverse"], "unread": "bold #ffffff",
        "nicks": ["#ffffff", "#d0d0d0", "#b0b0b0", "#e8e8e8"],
        "blue": "#9a9a9a", "green": "#6a6a6a",
    }),
    "light": palette(
        bg="#fafafa", fg="#303030", bar_bg="#d7e3fc", bar_fg="#102040", dim="#909090", muted="#b0b0b0",
        red="#c02020", green="#208030", yellow="#b07000", blue="#3050b0", magenta="#c02070", cyan="#008b8b",
        orange="#d2691e", bright="#000000", sidebar_bg="#f0f0f0", border="#d0d0d0",
        extra=("#9932cc", "#556b2f", "#4169e1", "#8b4513"), hilight="bold #ffffff on #c02070"),

    # ── editor palettes ───────────────────────────────────────────────────
    "dracula": palette(
        bg="#282a36", fg="#f8f8f2", bar_bg="#44475a", bar_fg="#f8f8f2", dim="#6272a4", muted="#565d80",
        red="#ff5555", green="#50fa7b", yellow="#f1fa8c", blue="#bd93f9", magenta="#ff79c6", cyan="#8be9fd",
        orange="#ffb86c", bright="#ffffff", sidebar_bg="#21222c", border="#44475a"),
    "nord": palette(
        bg="#2e3440", fg="#d8dee9", bar_bg="#3b4252", bar_fg="#eceff4", dim="#616e88", muted="#4c566a",
        red="#bf616a", green="#a3be8c", yellow="#ebcb8b", blue="#81a1c1", magenta="#b48ead", cyan="#88c0d0",
        orange="#d08770", bright="#eceff4", sidebar_bg="#2b303b", border="#434c5e", extra=("#8fbcbb", "#5e81ac"),
        hilight="bold #2e3440 on #88c0d0", hilight_text="bold #88c0d0"),
    "solarized-dark": palette(
        bg="#002b36", fg="#839496", bar_bg="#073642", bar_fg="#93a1a1", dim="#586e75", muted="#44606a",
        red="#dc322f", green="#859900", yellow="#b58900", blue="#268bd2", magenta="#d33682", cyan="#2aa198",
        orange="#cb4b16", bright="#eee8d5", sidebar_bg="#00252e", border="#073642", extra=("#6c71c4",)),
    "solarized-light": palette(
        bg="#fdf6e3", fg="#657b83", bar_bg="#eee8d5", bar_fg="#586e75", dim="#93a1a1", muted="#b3b8b0",
        red="#dc322f", green="#859900", yellow="#b58900", blue="#268bd2", magenta="#d33682", cyan="#2aa198",
        orange="#cb4b16", bright="#073642", sidebar_bg="#f5efdc", border="#e0d9c3", extra=("#6c71c4",),
        hilight="bold #fdf6e3 on #d33682"),
    "catppuccin": palette(  # mocha
        bg="#1e1e2e", fg="#cdd6f4", bar_bg="#313244", bar_fg="#cdd6f4", dim="#7f849c", muted="#585b70",
        red="#f38ba8", green="#a6e3a1", yellow="#f9e2af", blue="#89b4fa", magenta="#cba6f7", cyan="#89dceb",
        orange="#fab387", bright="#ffffff", sidebar_bg="#181825", border="#313244",
        extra=("#f5c2e7", "#94e2d5", "#74c7ec", "#b4befe", "#eba0ac", "#f2cdcd"), hilight="bold #1e1e2e on #f5c2e7",
        hilight_text="bold #f5c2e7"),
    "catppuccin-latte": palette(
        bg="#eff1f5", fg="#4c4f69", bar_bg="#dce0e8", bar_fg="#4c4f69", dim="#8c8fa1", muted="#acb0be",
        red="#d20f39", green="#40a02b", yellow="#df8e1d", blue="#1e66f5", magenta="#8839ef", cyan="#04a5e5",
        orange="#fe640b", bright="#11111b", sidebar_bg="#e6e9ef", border="#ccd0da",
        extra=("#ea76cb", "#179299", "#209fb5", "#7287fd", "#e64553", "#dd7878"), hilight="bold #eff1f5 on #ea76cb",
        hilight_text="bold #ea76cb"),
    "tokyonight": palette(
        bg="#1a1b26", fg="#c0caf5", bar_bg="#24283b", bar_fg="#c0caf5", dim="#565f89", muted="#414868",
        red="#f7768e", green="#9ece6a", yellow="#e0af68", blue="#7aa2f7", magenta="#bb9af7", cyan="#7dcfff",
        orange="#ff9e64", bright="#ffffff", sidebar_bg="#16161e", border="#292e42", extra=("#1abc9c", "#2ac3de", "#b4f9f8")),
    "one-dark": palette(
        bg="#282c34", fg="#abb2bf", bar_bg="#21252b", bar_fg="#abb2bf", dim="#5c6370", muted="#4b5263",
        red="#e06c75", green="#98c379", yellow="#e5c07b", blue="#61afef", magenta="#c678dd", cyan="#56b6c2",
        orange="#d19a66", bright="#ffffff", sidebar_bg="#21252b", border="#3e4451"),
    "monokai": palette(
        bg="#272822", fg="#f8f8f2", bar_bg="#3e3d32", bar_fg="#f8f8f2", dim="#75715e", muted="#5c5a4a",
        red="#f92672", green="#a6e22e", yellow="#e6db74", blue="#66d9ef", magenta="#ae81ff", cyan="#66d9ef",
        orange="#fd971f", bright="#ffffff", sidebar_bg="#1e1f1c", border="#49483e",
        hilight="bold #272822 on #f92672", hilight_text="bold #f92672"),
    "everforest": palette(
        bg="#2d353b", fg="#d3c6aa", bar_bg="#3d484d", bar_fg="#d3c6aa", dim="#859289", muted="#56635f",
        red="#e67e80", green="#a7c080", yellow="#dbbc7f", blue="#7fbbb3", magenta="#d699b6", cyan="#83c092",
        orange="#e69875", bright="#fdf6e3", sidebar_bg="#272e33", border="#475258"),
    "rose-pine": palette(
        bg="#191724", fg="#e0def4", bar_bg="#26233a", bar_fg="#e0def4", dim="#6e6a86", muted="#524f67",
        red="#eb6f92", green="#31748f", yellow="#f6c177", blue="#9ccfd8", magenta="#c4a7e7", cyan="#9ccfd8",
        orange="#ebbcba", bright="#ffffff", sidebar_bg="#1f1d2e", border="#403d52",
        hilight="bold #191724 on #eb6f92", hilight_text="bold #eb6f92"),
    "kanagawa": palette(
        bg="#1f1f28", fg="#dcd7ba", bar_bg="#2a2a37", bar_fg="#dcd7ba", dim="#727169", muted="#54546d",
        red="#e46876", green="#98bb6c", yellow="#e6c384", blue="#7e9cd8", magenta="#957fb8", cyan="#7fb4ca",
        orange="#ffa066", bright="#ffffff", sidebar_bg="#16161d", border="#363646", extra=("#d27e99", "#7aa89f", "#c8c093")),
    "gruvbox-light": palette(
        bg="#fbf1c7", fg="#3c3836", bar_bg="#ebdbb2", bar_fg="#3c3836", dim="#928374", muted="#bdae93",
        red="#9d0006", green="#79740e", yellow="#b57614", blue="#076678", magenta="#8f3f71", cyan="#427b58",
        orange="#af3a03", bright="#1d2021", sidebar_bg="#f2e5bc", border="#d5c4a1"),
    "github-dark": palette(
        bg="#0d1117", fg="#c9d1d9", bar_bg="#161b22", bar_fg="#c9d1d9", dim="#8b949e", muted="#484f58",
        red="#f85149", green="#3fb950", yellow="#d29922", blue="#58a6ff", magenta="#bc8cff", cyan="#39c5cf",
        orange="#db6d28", bright="#f0f6fc", sidebar_bg="#010409", border="#30363d", extra=("#db61a2",)),
    "github-light": palette(
        bg="#ffffff", fg="#24292f", bar_bg="#f6f8fa", bar_fg="#24292f", dim="#6e7781", muted="#afb8c1",
        red="#cf222e", green="#1a7f37", yellow="#9a6700", blue="#0969da", magenta="#8250df", cyan="#1b7c83",
        orange="#bc4c00", bright="#000000", sidebar_bg="#f6f8fa", border="#d0d7de", extra=("#bf3989",)),

    # ── retro ─────────────────────────────────────────────────────────────
    "bitchx": palette(  # CGA colours, cyan-on-blue bars
        bg="#000000", fg="#aaaaaa", bar_bg="#0000aa", bar_fg="#55ffff", dim="#555555", muted="#444444",
        red="#ff5555", green="#55ff55", yellow="#ffff55", blue="#5555ff", magenta="#ff55ff", cyan="#55ffff",
        orange="#aa5500", bright="#ffffff", border="#0000aa", extra=("#00aaaa", "#00aa00", "#aa00aa"),
        bracket="#55ffff", timestamp="#00aaaa"),
    "mirc": palette(  # classic mIRC: white window, mIRC colour codes for nicks
        bg="#ffffff", fg="#000000", bar_bg="#c0c0c0", bar_fg="#000000", dim="#7f7f7f", muted="#a0a0a0",
        red="#ff0000", green="#009300", yellow="#fc7f00", blue="#00007f", magenta="#9c009c", cyan="#009393",
        orange="#7f0000", bright="#000000", sidebar_bg="#ffffff", border="#808080",
        extra=("#0000fc", "#ff00ff", "#00fc00"), notice="bold #009300", hilight="bold #ffffff on #ff0000"),
    "matrix": palette(
        bg="#000000", fg="#00ff41", bar_bg="#003b00", bar_fg="#00ff41", dim="#008f11", muted="#005f0b",
        red="#aaff66", green="#00ff41", yellow="#88ff88", blue="#00cc33", magenta="#ccffcc", cyan="#33ff99",
        orange="#66ff66", bright="#ccffcc", border="#003b00", extra=("#00b32c", "#7dff9b"),
        hilight="bold #000000 on #00ff41", error="bold #ccffcc on #005f0b"),
    "amber": palette(  # monochrome amber CRT
        bg="#110a00", fg="#ffb000", bar_bg="#3d2800", bar_fg="#ffcc66", dim="#996a00", muted="#664600",
        red="#ffdd99", green="#ffc34d", yellow="#ffcc00", blue="#e69c00", magenta="#fff0cc", cyan="#ffbf33",
        orange="#ff9900", bright="#ffe0a3", border="#3d2800", hilight="bold #110a00 on #ffb000",
        error="bold #110a00 on #ff9900"),
    "synthwave": palette(  # synthwave '84
        bg="#262335", fg="#ffffff", bar_bg="#241b2f", bar_fg="#ff7edb", dim="#848bbd", muted="#495495",
        red="#fe4450", green="#72f1b8", yellow="#fede5d", blue="#36f9f6", magenta="#ff7edb", cyan="#36f9f6",
        orange="#f97e72", bright="#ffffff", sidebar_bg="#1e1a2b", border="#495495", extra=("#b893ce",),
        bracket="#ff7edb", hilight="bold #262335 on #fede5d", hilight_text="bold #fede5d"),
    "high-contrast": palette(
        bg="#000000", fg="#ffffff", bar_bg="#000080", bar_fg="#ffffff", dim="#c0c0c0", muted="#a0a0a0",
        red="#ff4040", green="#40ff40", yellow="#ffff00", blue="#4da6ff", magenta="#ff66ff", cyan="#00ffff",
        orange="#ffa500", bright="#ffffff", border="#ffffff", bracket="#ffff00",
        hilight="bold #000000 on #ffff00", hilight_text="bold #ffff00"),
}
