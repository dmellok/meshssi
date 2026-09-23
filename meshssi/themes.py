"""Colour themes. Each maps UI roles to Rich/Textual colours."""

THEMES: dict[str, dict] = {
    "irssi": {
        "background": "#000000", "foreground": "#d0d0d0", "bar_bg": "#00005f", "bar_fg": "#ffffff",
        "bracket": "#5f87ff", "timestamp": "grey50", "own_nick": "bold white", "hilight": "bold black on magenta",
        "hilight_text": "bold magenta", "notice": "bold blue", "error": "bold red", "ok": "bold green",
        "dim": "grey42", "meta": "grey35", "sidebar_bg": "#0a0a0a", "sidebar_border": "#303030",
        "act": ["grey62", "bold white", "bold magenta"], "unread": "bold yellow",
        "nicks": ["cyan", "green", "yellow", "magenta", "bright_blue", "bright_cyan", "bright_green",
                  "bright_yellow", "bright_magenta", "orange1", "orchid", "spring_green2", "deep_sky_blue1",
                  "light_salmon1", "khaki1", "plum1"],
    },
    "midnight": {
        "background": "#0b1020", "foreground": "#c8d3f5", "bar_bg": "#1e2a4a", "bar_fg": "#c8d3f5",
        "bracket": "#82aaff", "timestamp": "#5b6a95", "own_nick": "bold #ffffff", "hilight": "bold #0b1020 on #ff966c",
        "hilight_text": "bold #ff966c", "notice": "bold #82aaff", "error": "bold #ff757f", "ok": "bold #c3e88d",
        "dim": "#5b6a95", "meta": "#444a73", "sidebar_bg": "#0f1528", "sidebar_border": "#2f3b63",
        "act": ["#7a88cf", "bold #c8d3f5", "bold #ff966c"], "unread": "bold #ffc777",
        "nicks": ["#82aaff", "#c3e88d", "#ffc777", "#ff966c", "#c099ff", "#86e1fc", "#4fd6be", "#fca7ea",
                  "#ff757f", "#b4f9f8", "#f8bd96", "#a9b8e8"],
    },
    "gruvbox": {
        "background": "#1d2021", "foreground": "#ebdbb2", "bar_bg": "#3c3836", "bar_fg": "#ebdbb2",
        "bracket": "#d79921", "timestamp": "#928374", "own_nick": "bold #fbf1c7", "hilight": "bold #1d2021 on #fb4934",
        "hilight_text": "bold #fb4934", "notice": "bold #83a598", "error": "bold #fb4934", "ok": "bold #b8bb26",
        "dim": "#7c6f64", "meta": "#665c54", "sidebar_bg": "#232627", "sidebar_border": "#504945",
        "act": ["#a89984", "bold #fbf1c7", "bold #fb4934"], "unread": "bold #fabd2f",
        "nicks": ["#83a598", "#b8bb26", "#fabd2f", "#d3869b", "#8ec07c", "#fe8019", "#458588", "#98971a",
                  "#d79921", "#b16286", "#689d6a", "#d65d0e"],
    },
    "mono": {
        "background": "#000000", "foreground": "#c0c0c0", "bar_bg": "#303030", "bar_fg": "#ffffff",
        "bracket": "#808080", "timestamp": "#707070", "own_nick": "bold #ffffff", "hilight": "bold reverse",
        "hilight_text": "bold #ffffff", "notice": "bold #a0a0a0", "error": "bold #ffffff", "ok": "#e0e0e0",
        "dim": "#606060", "meta": "#505050", "sidebar_bg": "#0a0a0a", "sidebar_border": "#303030",
        "act": ["#808080", "bold #e0e0e0", "bold reverse"], "unread": "bold #ffffff",
        "nicks": ["#ffffff", "#d0d0d0", "#b0b0b0", "#e8e8e8"],
    },
    "light": {
        "background": "#fafafa", "foreground": "#303030", "bar_bg": "#d7e3fc", "bar_fg": "#102040",
        "bracket": "#4060c0", "timestamp": "#909090", "own_nick": "bold #000000", "hilight": "bold #ffffff on #c02070",
        "hilight_text": "bold #c02070", "notice": "bold #3050b0", "error": "bold #c02020", "ok": "bold #208030",
        "dim": "#a0a0a0", "meta": "#b0b0b0", "sidebar_bg": "#f0f0f0", "sidebar_border": "#d0d0d0",
        "act": ["#909090", "bold #202020", "bold #c02070"], "unread": "bold #b07000",
        "nicks": ["#1f6fb2", "#2e8b57", "#b8860b", "#9932cc", "#c71585", "#008b8b", "#d2691e", "#556b2f",
                  "#4169e1", "#8b4513"],
    },
}
