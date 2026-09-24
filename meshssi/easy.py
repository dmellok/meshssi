"""The friendlier, mouse-driven layer: node cards, command hints, the command palette, window list, toolbar."""

from rich.style import Style
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.command import DiscoveryHit, Hit, Hits, Provider
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from . import geo
from .commands import ALIASES, COMMANDS
from .util import ago, clean

TYPES = {0: "unknown", 1: "chat node", 2: "repeater", 3: "room server", 4: "sensor"}
CONTACT_ARGS = ("<contact", "<node", "<repeater", "<room")


def click(action: str) -> Style:
    return Style.from_meta({"@click": action})


# ── node card: click a name anywhere ─────────────────────────────────────────────────────────────────
class NodeCard(ModalScreen):
    """What you can do with a node. Buttons either run a command or pre-fill the input when it needs more."""

    BINDINGS = [Binding("escape", "dismiss", show=False)]
    DEFAULT_CSS = """
    NodeCard { align: center middle; background: $background 60%; }
    #card { width: 64; height: auto; max-height: 90%; padding: 1 2; border: round $accent; background: $panel; }
    #card-title { text-style: bold; margin-bottom: 1; }
    #card-buttons { height: auto; margin-top: 1; layout: grid; grid-size: 3; grid-gutter: 1 1; grid-rows: 1; }
    #card-buttons Button { width: 100%; height: 1; }
    """

    def __init__(self, app_, name: str, contact: dict | None, heard: dict | None, pending: dict | None):
        super().__init__()
        self.meshssi = app_
        self.node_name = name
        self.contact = contact
        self.heard = heard
        self.pending = pending
        self.actions: dict[str, tuple[str, bool]] = {}  # button id -> (command, run now?)

    def _action(self, label: str, command: str, run: bool = True, variant: str = "default") -> Button:
        bid = f"act{len(self.actions)}"
        self.actions[bid] = (command, run)
        return Button(label, id=bid, variant=variant, compact=True)

    def compose(self) -> ComposeResult:
        app, c, h = self.meshssi, self.contact, self.heard
        name = self.node_name
        quoted = f'"{name}"' if " " in name else name
        kind = (c or h or self.pending or {}).get("type", 0)
        info = Text()
        if c:
            info.append(f"{TYPES.get(kind, 'node')} · in your contacts\n", "bold")
            info.append(f"key     {c['public_key'][:16]}…\n")
            info.append(f"route   {app.path_str(c)}\n")
            info.append(f"advert  {ago(c.get('last_advert'))}\n")
        elif self.pending:
            info.append(f"{TYPES.get(kind, 'node')} · heard, waiting to be added\n", "bold")
        elif h:
            info.append(f"{TYPES.get(kind, 'node')} · heard over the air, not a contact\n", "bold")
        else:
            info.append("only seen in chat: no advert heard from this name yet\n", "bold")
        src = c or h or {}
        lat, lon = src.get("adv_lat", src.get("lat")), src.get("adv_lon", src.get("lon"))
        if geo.has_fix(lat, lon):
            info.append(f"where   {lat:.4f}, {lon:.4f}  {geo.describe(app.my_pos, lat, lon)}\n")
        if h and h.get("snr") is not None:
            info.append(f"heard   {h['snr']:+.1f} dB, {ago(h.get('last'))}\n")

        buttons = []
        if c:
            if kind == 1:
                buttons.append(self._action("Message", f"query {quoted}", variant="primary"))
            elif kind in (2, 3):
                buttons.append(self._action("Open window", f"query {quoted}", variant="primary"))
            buttons.append(self._action("Whois", f"whois {quoted}"))
            if kind == 2:
                buttons += [self._action("Log in…", f"login {quoted} ", run=False), self._action("Status", f"rstatus {quoted}"),
                            self._action("Neighbours", f"neighbours {quoted}"), self._action("Trace", f"trace {quoted}"),
                            self._action("Watch", f"watch {quoted}"), self._action("CLI command…", f"rcmd {quoted} ", run=False)]
            elif kind == 3:
                buttons += [self._action("Join room…", f"room {quoted} ", run=False), self._action("Trace", f"trace {quoted}")]
            elif kind == 4:
                buttons.append(self._action("Telemetry", f"telemetry {quoted}"))
            buttons.append(self._action("Find route", f"path {quoted}"))
            if kind == 1:
                buttons.append(self._action("Trace route", f"trace {quoted}"))
            buttons.append(self._action("Share card", f"qr {quoted}"))
        elif self.pending:
            buttons.append(self._action("Add contact", f"accept {self.pending['public_key'][:12]}", variant="primary"))
        if app.win.kind == "channel":
            buttons.append(self._action(f"Mention in {app.win.name}", f"@[{name}] ", run=False))
        if geo.has_fix(lat, lon):
            buttons.append(self._action("Show on map", f"map center {quoted}"))
        if c:
            buttons.append(self._action("Remove…", f"rmcontact {quoted}", run=False, variant="error"))
        buttons.append(self._action("Close", "", variant="default"))
        with Vertical(id="card"):
            yield Static(Text(clean(name), style="bold " + app.nick_color(name)), id="card-title")
            yield Static(info)
            with Horizontal(id="card-buttons"):
                yield from buttons

    def on_button_pressed(self, event: Button.Pressed) -> None:
        command, run = self.actions.get(event.button.id or "", ("", True))
        self.dismiss()
        if not command:
            return
        if command.startswith("@["):  # a mention: add to what's being typed
            inp = self.meshssi.query_one("#input")
            inp.value = command + inp.value
        elif run:
            self.meshssi.run_worker(self.meshssi.run_command(command), group="card")
        else:  # needs more from you (a password, a command...): pre-fill the input
            inp = self.meshssi.query_one("#input")
            inp.value = "/" + command
            inp.focus()


# ── command hints above the input ────────────────────────────────────────────────────────────────────
def hint_for(app, line: str, limit: int = 8) -> Text | None:
    """What to show above the input while a command is being typed, or None."""
    if not line.startswith("/") or line.startswith("//"):
        return None
    name, sep, args = line[1:].partition(" ")
    st = app.st
    out = Text(no_wrap=True, overflow="ellipsis")
    if not sep:  # still typing the command name: list matches, click to pick
        q = name.lower()
        names = sorted(n for n in COMMANDS if n.startswith(q)) + sorted(
            a for a in ALIASES if a.startswith(q) and ALIASES[a] not in (n for n in COMMANDS if n.startswith(q)))
        names += sorted(n for n in COMMANDS if q and q in n and not n.startswith(q))
        if not names:
            out.append(f"no command starts with /{name} — F1 lists them all", st["dim"])
            return out
        for i, n in enumerate(names[:limit]):
            _, _, usage, help_ = COMMANDS[ALIASES.get(n, n)]
            if i:
                out.append("\n")
            out.append(f"/{n}", Style.parse("bold") + click(f"app.pick_command('{n}')"))
            out.append(f"  {usage.split(' ', 1)[1] if ' ' in usage else ''}", st["meta"])
            out.append(f"  {help_}", st["dim"])
        if len(names) > limit:
            out.append(f"\n… {len(names) - limit} more (keep typing, or F1)", st["dim"])
        return out
    real = ALIASES.get(name.lower(), name.lower())
    if real not in COMMANDS:
        return None
    _, _, usage, help_ = COMMANDS[real]
    out.append(usage, "bold")
    out.append(f"  {help_}", st["dim"])
    if any(a in usage for a in CONTACT_ARGS) and app.mc:  # suggest who, from what's typed so far
        frag = args.strip().strip('"')
        shown = 0
        for c in sorted(app.mc.contacts.values(), key=lambda c: -(c.get("last_advert") or 0)):
            if frag and not c["adv_name"].lower().startswith(frag.lower().split(" ")[0]):
                continue
            nm = clean(c["adv_name"])
            out.append("\n  " if shown % 4 == 0 else "   ")
            out.append(nm, Style.parse(app.nick_color(nm)) + click(f"app.pick_contact('{c['public_key']}')"))
            out.append(f" {TYPES.get(c['type'], '?').split()[0]}", st["meta"])
            shown += 1
            if shown >= 12:
                break
    return out


# ── F1: every command, searchable ────────────────────────────────────────────────────────────────────
class MeshCommands(Provider):
    def _run(self, name: str):
        app = self.app
        usage = COMMANDS[name][2]
        if "<" in usage:  # needs arguments: pre-fill and let the hints guide the rest
            def fill():
                inp = app.query_one("#input")
                inp.value = f"/{name} "
                inp.focus()
            return fill
        return lambda: app.run_worker(app.run_command(name), group="palette")

    async def discover(self) -> Hits:
        for name, (_, cat, usage, help_) in sorted(COMMANDS.items(), key=lambda kv: (kv[1][1], kv[0])):
            yield DiscoveryHit(Text.assemble((f"/{name}", "bold"), f"  {help_}"), self._run(name), text=f"/{name} {help_}")

    async def search(self, query: str) -> Hits:
        matcher = self.matcher(query)
        for name, (_, cat, usage, help_) in COMMANDS.items():
            text = f"/{name} {usage} {help_} {cat}"
            score = matcher.match(name) * 3 + matcher.match(text)  # the command's own name counts most
            if score > 0:
                yield Hit(score, matcher.highlight(f"/{name}  {help_}"), self._run(name), text=text, help=usage)


# ── easy layout: window list, toolbar ────────────────────────────────────────────────────────────────
def render_window_list(app) -> Text:
    st = app.st
    out = Text(no_wrap=True, overflow="ellipsis")
    out.append("windows\n", "bold underline")
    for i, w in enumerate(app.windows):
        current = i == app.current
        mark = {0: "", 1: " ·", 2: " ●", 3: " ●"}[w.activity]
        style = "bold reverse" if current else (st["act"][w.activity - 1] if w.activity else "")
        out.append(f"{i + 1:>2} {clean(w.name)[:18]}{mark}", Style.parse(style or "none") + click(f"app.goto({i + 1})"))
        out.append("\n")
    out.append("\n+ join channel", Style.parse(st["dim"]) + click("app.fill('/join #')"))
    out.append("\n+ message someone", Style.parse(st["dim"]) + click("app.fill('/query ')"))
    return out


TOOLBAR = [("Channels", "fill:/join #"), ("Message", "fill:/query "), ("Map", "map"), ("Packets", "rf"),
           ("Graphs", "graphs"), ("Repeaters", "dash"), ("Themes", "theme"), ("Commands", "palette")]


def key_hints(app) -> Text:
    st = app.st
    out = Text(no_wrap=True, overflow="ellipsis")
    for key, what in (("F1", "commands"), ("F2", "people"), ("F3", "windows"), ("click", "a name for actions"),
                      ("click", "2» to trace"), ("/", "shows hints"), ("Tab", "completes")):
        out.append(f" {key} ", "reverse").append(f" {what}  ", st["dim"])
    return out

