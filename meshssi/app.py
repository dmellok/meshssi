"""The TUI: irssi-style windows over a MeshCore companion connection."""

import asyncio
import collections
import fnmatch
import time
import uuid
from dataclasses import dataclass, field

from meshcore import EventType, MeshCore
from rich.cells import cell_len
from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Input, RichLog, Static

from . import geo, packets
from .commands import CommandsMixin
from .config import Config
from .notify import desktop_notify
from .plugins import PluginManager
from .store import Store
from .themes import THEMES
from .util import ago, expand_shortcodes, linkify, name_forms, name_matches, pick_color, split_utf8

CONTACT_TYPES = {0: "?", 1: "chat", 2: "repeater", 3: "room", 4: "sensor"}
TYPE_GLYPH = {1: "@", 2: "R", 3: "#", 4: "S"}
MAX_TEXT = 150  # bytes per mesh text packet, leaving headroom under the firmware's 160


@dataclass
class Window:
    kind: str  # "status" | "channel" | "query" | "rf" | "view"
    key: str
    name: str
    channel_idx: int | None = None
    pubkey: str | None = None
    view: str | None = None  # for kind == "view": map | graphs | dash
    recs: list[dict] = field(default_factory=list)
    activity: int = 0  # 0 none, 1 events, 2 messages, 3 hilight/DM
    speakers: dict[str, float] = field(default_factory=dict)
    marker: str | None = None  # id of the last record seen before leaving the window


class PromptInput(Input):
    """Input line with history and irssi-ish tab completion."""

    BINDINGS = [
        Binding("tab", "complete", show=False),
        Binding("up", "history(-1)", show=False),
        Binding("down", "history(1)", show=False),
    ]

    def __init__(self, **kw):
        super().__init__(**kw)
        self.history: list[str] = []
        self.hist_pos = 0
        self._comp: tuple[int, list[str], int] | None = None  # (start, candidates, index)
        self._comp_value = ""

    def remember(self, line: str) -> None:
        if line and (not self.history or self.history[-1] != line):
            self.history.append(line)
        self.hist_pos = len(self.history)

    def action_history(self, step: int) -> None:
        if self._map_mode():
            self.app.map_key("up" if step < 0 else "down")
            return
        if not self.history:
            return
        self.hist_pos = max(0, min(len(self.history), self.hist_pos + step))
        self.value = self.history[self.hist_pos] if self.hist_pos < len(self.history) else ""
        self.cursor_position = len(self.value)

    def _map_mode(self) -> bool:
        win = self.app.win
        return win.kind == "view" and win.view == "map" and not self.value

    async def _on_key(self, event: events.Key) -> None:
        if self._map_mode() and event.character in ("+", "=", "-", "_", "0", "c"):
            event.stop()
            event.prevent_default()
            self.app.map_key(event.character)
            return
        await super()._on_key(event)

    def action_cursor_left(self, select: bool = False) -> None:
        if self._map_mode():
            self.app.map_key("left")
        else:
            super().action_cursor_left(select)

    def action_cursor_right(self, select: bool = False) -> None:
        if self._map_mode():
            self.app.map_key("right")
        else:
            super().action_cursor_right(select)

    def action_complete(self) -> None:
        if self._comp is None or self.value != self._comp_value:
            before = self.value[: self.cursor_position]
            self._comp = self.app.completions(before)
            if not self._comp[1]:
                self._comp = None
                return
        start, cands, idx = self._comp
        choice = cands[idx % len(cands)]
        self.value = self.value[:start] + choice
        self.cursor_position = len(self.value)
        self._comp = (start, cands, idx + 1)
        self._comp_value = self.value


class MeshssiApp(CommandsMixin, App):
    TITLE = "meshssi"
    CSS = """
    #topic { height: 1; padding: 0 1; }
    #main { height: 1fr; }
    #panes { width: 1fr; }
    #log, #log2 { border: none; padding: 0 1; scrollbar-size-vertical: 1; }
    #log2 { height: 40%; }
    #view { padding: 0 1; height: 1fr; }
    #nicklist { width: 32; padding: 0 1; text-wrap: nowrap; text-overflow: ellipsis; }
    #statusbar { height: 1; padding: 0 1; }
    #promptrow { height: 1; }
    #prompt { width: auto; padding: 0 0 0 1; }
    #input { border: none; height: 1; padding: 0 1; }
    #input:focus { border: none; }
    """
    BINDINGS = [
        *[Binding(f"alt+{n % 10}", f"goto({n})", show=False, priority=True) for n in range(1, 11)],
        *[Binding(f"escape,{n % 10}", f"goto({n})", show=False) for n in range(1, 11)],
        Binding("ctrl+n,alt+right", "cycle(1)", show=False, priority=True),
        Binding("ctrl+p,alt+left", "cycle(-1)", show=False, priority=True),
        Binding("alt+a,ctrl+a", "next_active", show=False, priority=True),
        Binding("pageup", "scroll(-1)", show=False, priority=True),
        Binding("pagedown", "scroll(1)", show=False, priority=True),
        Binding("f2", "toggle_nicklist", show=False, priority=True),
        Binding("ctrl+c", "quit", show=False, priority=True),
    ]

    def __init__(self, target: str, baud: int = 115200, config: Config | None = None):
        super().__init__()
        self.cfg = config or Config()
        self.target = target
        self.baud = baud
        self.theme_name = self.cfg.get("ui.theme") if self.cfg.get("ui.theme") in THEMES else "irssi"
        self.mc: MeshCore | None = None
        self.io = asyncio.Lock()  # serialize request/response commands to the radio
        self.store: Store | None = None
        self.windows: list[Window] = [Window("status", "status", "(status)")]
        self.current = 0
        self.split_win: Window | None = None
        self.self_info: dict = {}
        self.device_info: dict = {}
        self.channels: dict[int, dict] = {}
        self.stats: dict = {}
        self.acks: dict[str, tuple[Window, dict, asyncio.Future]] = {}  # expected ack code -> message
        self.pending_confirm: tuple[str, float] | None = None
        self.connected = False
        self.drops: list[float] = []  # recent disconnect times, to spot another client fighting for the radio
        # mesh awareness
        self.heard: dict[str, dict] = {}  # pubkey -> {name, type, lat, lon, last, snr, rssi, hops}
        self.rf: collections.deque = collections.deque(maxlen=3000)
        self.samples: collections.deque = collections.deque(maxlen=720)
        self.snr_hist: dict[str, collections.deque] = {}
        self.sent_chan: collections.deque = collections.deque(maxlen=30)  # own channel recs, for heard-by counts
        self.dash: dict[str, tuple[float, dict | None]] = {}
        self.away: str | None = None
        self.away_replied: set[str] = set()
        self.ignored = 0
        self.term_focused = True
        self.last_advert = time.time()
        self._contacts_refresh: asyncio.Task | None = None
        self.plugins = PluginManager(self)

    # ── layout ────────────────────────────────────────────────────────────
    def compose(self) -> ComposeResult:
        yield Static(id="topic")
        with Horizontal(id="main"):
            with Vertical(id="panes"):
                yield RichLog(id="log2", wrap=True, markup=False, highlight=False, max_lines=3000)
                yield RichLog(id="log", wrap=True, markup=False, highlight=False, max_lines=5000)
                yield Static(id="view")
            yield Static(id="nicklist")
        yield Static(id="statusbar")
        with Horizontal(id="promptrow"):
            yield Static(id="prompt")
            yield PromptInput(id="input")

    def on_mount(self) -> None:
        self.query_one("#log2").display = False
        self.query_one("#view").display = False
        self.query_one("#nicklist").display = bool(self.cfg.get("ui.nicklist", True))
        self.apply_theme()
        self.query_one("#input").focus()
        self.status("meshssi — an irssi-style MeshCore client. /help for commands.")
        if self.cfg.error:
            self.status(self.cfg.error, "error")
        for msg in self.plugins.load_all():
            self.status(*msg)
        self.refresh_chrome()
        self.set_interval(1, self.refresh_statusbar)
        self.set_interval(2, self.refresh_view)
        self.set_interval(30, self.poll_stats)
        self.set_interval(60, self.periodic)
        self.run_worker(self.connect(), exclusive=True, group="connect")

    @property
    def st(self) -> dict:
        return THEMES[self.theme_name]

    def nick_color(self, nick: str) -> str:
        return pick_color(nick, self.st["nicks"])

    def apply_theme(self) -> None:
        t = self.st
        self.screen.styles.background = t["background"]
        for sel in ("#topic", "#statusbar"):
            w = self.query_one(sel)
            w.styles.background, w.styles.color = t["bar_bg"], t["bar_fg"]
        for sel in ("#log", "#log2", "#view", "#input", "#promptrow", "#prompt"):
            w = self.query_one(sel)
            w.styles.background, w.styles.color = t["background"], t["foreground"]
            w.styles.scrollbar_background = t["background"]
            w.styles.scrollbar_background_hover = t["background"]
            w.styles.scrollbar_background_active = t["background"]
            w.styles.scrollbar_color = t["sidebar_border"]
            w.styles.scrollbar_color_hover = t["dim"]
            w.styles.scrollbar_color_active = t["dim"]
        self.query_one("#log2").styles.border_bottom = ("solid", t["sidebar_border"])
        nl = self.query_one("#nicklist")
        nl.styles.background = t["sidebar_bg"]
        nl.styles.color = t["foreground"]
        nl.styles.border_left = ("solid", t["sidebar_border"])

    @property
    def win(self) -> Window:
        return self.windows[self.current]

    @property
    def my_name(self) -> str:
        return self.self_info.get("name", "me")

    @property
    def my_pos(self) -> tuple[float, float] | None:
        si = self.self_info
        return (si["adv_lat"], si["adv_lon"]) if si and geo.has_fix(si.get("adv_lat"), si.get("adv_lon")) else None

    # ── output ────────────────────────────────────────────────────────────
    def render_rec(self, rec: dict) -> Text:
        t = self.st
        ts = time.strftime(self.cfg.get("ui.timestamp_format", "%H:%M"), time.localtime(rec.get("t", time.time())))
        kind = rec.get("k")
        if kind == "rf":
            s = rec["s"]
            dist = geo.describe(self.my_pos, s.get("adv_lat"), s.get("adv_lon"))
            return packets.render(s, t, self.nick_color, dist)
        if kind == "raw":  # pre-rendered block (QR codes, tables)
            return Text.from_markup(rec.get("text", ""))
        line = Text(f"{ts} ", style=t["timestamp"])
        if kind == "msg":
            nick = rec.get("nick", "?")
            if self.cfg.get("ui.show_hops", True):  # a fixed-width hop-count column before the nick
                h = rec.get("hops")
                if h is None:
                    line.append("    ")
                else:
                    h = 0 if h == 255 else h  # 255 = sent direct along a stored route
                    line.append(f"{h:>2}» ", t["meta"] if h else t["good"])
            if rec.get("own"):
                line.append("<", t["timestamp"]).append(nick, t["own_nick"]).append("> ", t["timestamp"])
            elif rec.get("hl"):
                line.append("<", t["timestamp"]).append(nick, t["hilight"]).append("> ", t["timestamp"])
            else:
                line.append("<", t["timestamp"]).append(nick, self.nick_color(nick)).append("> ", t["timestamp"])
            body = Text()
            self._append_body(body, rec.get("text", ""), t["hilight_text"] if rec.get("hl") else "")
            line.append(linkify(body))
            st = rec.get("st")
            if st == "pending":
                line.append(" …" + (f"{rec['try']}" if rec.get("try", 1) > 1 else ""), t["dim"])
            elif st == "ok":
                line.append(" ✓", t["ok"])
            elif st == "fail":
                line.append(" ✗", t["error"])
            if self.cfg.get("ui.show_signal", True):
                meta = []
                if self.cfg.get("ui.show_snr", False) and rec.get("snr") is not None:
                    meta.append(f"{rec['snr']:+.1f}dB")
                if rec.get("heard"):
                    meta.append(f"heard ×{rec['heard']}")
                if rec.get("rtt"):
                    meta.append(f"{rec['rtt'] / 1000:.1f}s")
                if self.cfg.get("ui.show_paths") and rec.get("path"):
                    meta.append("via " + rec["path"])
                if meta:
                    line.append(f"  [{' · '.join(meta)}]", t["meta"])
        elif kind == "reply":  # repeater / room CLI output
            nick = rec.get("nick", "?")
            line.append("-", t["notice"]).append(nick, "bold " + self.nick_color(nick))
            line.append("- ", t["notice"]).append(rec.get("text", ""))
        else:
            lvl = rec.get("lvl", "info")
            marker, style = {
                "info": ("-!- ", t["notice"]),
                "error": ("-!- ", t["error"]),
                "ok": ("-!- ", t["ok"]),
                "join": ("-!- ", t["ok"]),
                "dim": ("--- ", t["dim"]),
            }.get(lvl, ("-!- ", t["notice"]))
            line.append(marker, style)
            body = Text.from_markup(rec.get("text", "")) if rec.get("markup") else Text(rec.get("text", ""))
            body.stylize(t["error"].replace("bold ", "") if lvl == "error" else (t["dim"] if lvl == "dim" else ""))
            line.append(linkify(body))
        return line

    def _append_body(self, line: Text, body: str, base: str) -> None:
        """Render @[name] mentions (MeshCore mention syntax) as coloured @name."""
        i = 0
        while True:
            j = body.find("@[", i)
            k = body.find("]", j + 2) if j >= 0 else -1
            if j < 0 or k < 0:
                line.append(body[i:], base)
                return
            line.append(body[i:j], base)
            name = body[j + 2 : k]
            line.append("@" + name, "bold " + ("reverse " if name == self.my_name else "") + self.nick_color(name))
            i = k + 1

    def add(self, win: Window, rec: dict, persist: bool = True, activity: int = 1) -> dict:
        rec.setdefault("t", time.time())
        rec.setdefault("id", uuid.uuid4().hex[:10])
        win.recs.append(rec)
        limit = int(self.cfg.get("ui.scrollback", 2000))
        if len(win.recs) > limit:
            del win.recs[:-limit]
        if persist and self.store and win.kind in ("channel", "query"):
            self.store.append(win.key, rec)
        if win is self.win and win.kind != "view":
            self._write(rec)
        elif activity > win.activity:
            win.activity = activity
            self.refresh_statusbar()
        if win is self.split_win and win is not self.win:
            self._write(rec, "#log2")
        return rec

    def status(self, text: str, lvl: str = "info", markup: bool = False, win: Window | None = None) -> None:
        """Notice into a window (default: status)."""
        target = win or self.windows[0]
        self.add(target, {"k": "notice", "text": text, "lvl": lvl, "markup": markup}, activity=1)

    def echo(self, text: str, lvl: str = "info", markup: bool = False) -> None:
        """Command output: into the current window (status for view windows), never persisted."""
        target = self.win if self.win.kind not in ("view", "rf") else self.windows[0]
        self.add(target, {"k": "notice", "text": text, "lvl": lvl, "markup": markup}, persist=False)

    def echo_raw(self, markup: str) -> None:
        target = self.win if self.win.kind not in ("view", "rf") else self.windows[0]
        self.add(target, {"k": "raw", "text": markup}, persist=False)

    def wrapped(self, rec: dict, width: int) -> Text:
        """Render a record, wrapping long lines with a hanging indent so text never runs under the
        timestamp/nick column (irssi-style)."""
        line = self.render_rec(rec)
        plain = line.plain
        kind = rec.get("k")
        indent = 0
        if kind == "msg":
            indent = plain.find("> ") + 2
        elif kind == "reply":
            indent = plain.find("- ", plain.find("-") + 1) + 2
        elif kind == "notice":
            indent = max(plain.find("-!- "), plain.find("--- ")) + 4
        head_cells = cell_len(plain[:indent]) if indent > 1 else 0
        if not head_cells or head_cells > width // 2 or cell_len(plain) <= width:
            return line
        head, body = line[:indent], line[indent:]
        out = Text(end="")
        for i, part in enumerate(body.wrap(self.console, width - head_cells)):
            if i:
                out.append("\n" + " " * head_cells)
            else:
                out.append(head)
            out.append(part)
        return out

    def _write(self, rec: dict, sel: str = "#log") -> None:
        log = self.query_one(sel, RichLog)
        # RichLog measures Text against the console width, not the pane, so give it the pane width
        width = log.scrollable_content_region.width
        if width > 10:
            log.write(self.wrapped(rec, width), width=width)
        else:
            log.write(self.render_rec(rec))

    def redraw(self) -> None:
        w = self.win
        is_view = w.kind == "view"
        self.query_one("#log").display = not is_view
        self.query_one("#view").display = is_view
        self.redraw_split()
        if is_view:
            self.refresh_view()
            self.call_after_refresh(self.refresh_view)
            return
        log = self.query_one("#log", RichLog)
        log.clear()
        recs = w.recs[-1000:]
        marker_at = next((i for i, r in enumerate(recs) if r.get("id") == w.marker), None)
        for i, rec in enumerate(recs):
            self._write(rec)
            if marker_at is not None and i == marker_at and i < len(recs) - 1:
                width = max(10, log.scrollable_content_region.width)
                side = "─" * max(2, width // 2 - 4)
                log.write(Text(f"{side} new {side}", self.st["unread"]), width=width)
        log.scroll_end(animate=False)

    def redraw_split(self) -> None:
        log2 = self.query_one("#log2", RichLog)
        show = self.split_win is not None and self.split_win is not self.win and self.split_win in self.windows
        log2.display = show
        if show:
            self.call_after_refresh(self._fill_split)  # once the pane has its real width

    def _fill_split(self) -> None:
        if not self.split_win:
            return
        log2 = self.query_one("#log2", RichLog)
        log2.clear()
        width = max(10, log2.scrollable_content_region.width)
        label = f" {self.split_win.name} · window {self.windows.index(self.split_win) + 1} "
        side = "─" * max(2, (width - cell_len(label)) // 2)
        log2.write(Text(f"{side}{label}{side}", self.st["notice"]), width=width)
        for rec in self.split_win.recs[-300:]:
            self._write(rec, "#log2")
        log2.scroll_end(animate=False)

    def on_resize(self) -> None:
        self.call_after_refresh(self.redraw)

    def on_app_focus(self, event: events.AppFocus) -> None:
        self.term_focused = True

    def on_app_blur(self, event: events.AppBlur) -> None:
        self.term_focused = False

    def alert(self, win: Window, title: str, body: str) -> None:
        if self.cfg.get("notify.bell", True) and (win is not self.win or not self.term_focused):
            self.bell()
        if self.cfg.get("notify.desktop", True) and (not self.term_focused or not self.cfg.get("notify.only_when_unfocused", True)):
            self.run_worker(desktop_notify(title, body), group="notify")

    # ── chrome (topic, status bar, nicklist, prompt) ──────────────────────
    def refresh_chrome(self) -> None:
        self.refresh_topic()
        self.refresh_statusbar()
        self.refresh_nicklist()
        self.query_one("#prompt", Static).update(Text(f"[{self.win.name}]", "bold"))

    def refresh_topic(self) -> None:
        w = self.win
        if w.kind == "status":
            if self.connected:
                d = self.device_info
                t = f"{self.my_name} on {self.target} · {d.get('model', '?')} · fw {d.get('ver', '?')} ({d.get('fw_build', '?')})"
            else:
                t = f"meshssi · not connected ({self.target})"
        elif w.kind == "channel":
            ch = self.channels.get(w.channel_idx, {})
            kind = "public" if w.channel_idx == 0 else ("hashtag" if w.name.startswith("#") else "private")
            t = f"{w.name} · slot {w.channel_idx} · {kind} channel · hash {ch.get('channel_hash', '?')} · {len(w.speakers)} heard"
        elif w.kind == "rf":
            t = f"(rf) packet monitor · {len(self.rf)} packets · everything the radio hears, decrypted where you hold the key"
        elif w.kind == "view":
            t = {"map": "(map) nodes with a known location, from contacts and adverts heard over the air",
                 "graphs": "(graphs) noise floor, signal and traffic over time",
                 "dash": f"(dash) repeaters you /watch · status polled every {self.cfg.get('dashboard.interval', 10)} min",
                 }.get(w.view, w.name)
        else:
            c = self.contact(w.pubkey)
            if c:
                dist = geo.describe(self.my_pos, c.get("adv_lat"), c.get("adv_lon"))
                t = (f"{c['adv_name']} · {CONTACT_TYPES.get(c['type'], '?')} · {self.path_str(c)} · advert {ago(c.get('last_advert'))}"
                     + (f" · {dist}" if dist else "") + f" · {c['public_key'][:12]}")
            else:
                t = f"{w.name} · unknown contact {w.pubkey}"
        self.query_one("#topic", Static).update(Text(t, no_wrap=True, overflow="ellipsis"))

    def refresh_statusbar(self) -> None:
        t = self.st
        bar = Text(no_wrap=True, overflow="ellipsis")
        br = ("[", t["bracket"])
        er = ("] ", t["bracket"])
        bar.append(*br).append(time.strftime("%H:%M")).append(*er)
        bar.append(*br).append(self.my_name, "bold")
        bar.append(f"({'+' if self.connected else 'offline'})", t["status_ok"] if self.connected else t["status_bad"])
        if self.away is not None:
            bar.append(" (away)", t["unread"])
        bar.append(*er)
        bar.append(*br).append(f"{self.current + 1}:").append(self.win.name, "bold").append(*er)
        act = [(i, w.activity) for i, w in enumerate(self.windows) if w.activity and i != self.current]
        if act:
            bar.append(*br).append("Act: ")
            for n, (i, a) in enumerate(act):
                if n:
                    bar.append(",")
                bar.append(str(i + 1), t["act"][a - 1])
            bar.append(*er)
        si = self.self_info
        if si:
            bar.append(*br).append(f"{si['radio_freq']:.3f}/{si['radio_bw']:g}/SF{si['radio_sf']}/CR{si['radio_cr']} {si['tx_power']}dBm").append(*er)
        if "noise_floor" in self.stats:
            bar.append(*br).append(f"nf {self.stats['noise_floor']}dBm").append(*er)
        mv = self.stats.get("battery_mv")
        if mv:
            bar.append(*br).append(f"bat {mv / 1000:.2f}V").append(*er)
        self.query_one("#statusbar", Static).update(bar)

    def refresh_nicklist(self) -> None:
        w = self.win
        out = Text(no_wrap=True, overflow="ellipsis")
        dim = self.st["dim"]
        if w.kind == "channel":
            out.append(f"heard in {w.name}\n", "bold underline")
            for nick, ts in sorted(w.speakers.items(), key=lambda kv: -kv[1]):
                out.append(nick, self.nick_color(nick)).append(f"  {ago(ts).replace(' ago', '')}\n", dim)
        elif w.kind == "query" and (c := self.contact(w.pubkey)):
            out.append(f"{c['adv_name']}\n", "bold underline")
            out.append(f"type   {CONTACT_TYPES.get(c['type'])}\n")
            out.append(f"key    {c['public_key'][:12]}\n")
            out.append(f"route  {self.path_str(c)}\n")
            out.append(f"advert {ago(c.get('last_advert'))}\n")
            if geo.has_fix(c.get("adv_lat"), c.get("adv_lon")):
                out.append(f"loc    {c['adv_lat']:.4f}\n       {c['adv_lon']:.4f}\n")
                if d := geo.describe(self.my_pos, c["adv_lat"], c["adv_lon"]):
                    out.append(f"dist   {d}\n")
            if (h := self.heard.get(c["public_key"])) and h.get("snr") is not None:
                out.append(f"heard  {h['snr']:+.1f}dB {ago(h.get('last')).replace(' ago', '')}\n")
            if c["public_key"] in self.dash:
                out.append("\n/watch'ed — see (dash)\n", dim)
        else:
            contacts = sorted(self.mc.contacts.values() if self.mc else [], key=lambda c: -c.get("last_advert", 0))
            out.append(f"contacts ({len(contacts)})\n", "bold underline")
            for c in contacts:
                out.append(TYPE_GLYPH.get(c["type"], "?") + " ", dim)
                out.append(c["adv_name"], self.nick_color(c["adv_name"]))
                out.append(f"  {ago(c.get('last_advert')).replace(' ago', '')}\n", dim)
            others = [h for k, h in self.heard.items() if not (self.mc and k in self.mc.contacts)]
            if others:
                out.append(f"\nheard, not added ({len(others)})\n", "bold underline")
                for h in sorted(others, key=lambda h: -h.get("last", 0))[:30]:
                    out.append(TYPE_GLYPH.get(h.get("type"), "?") + " ", dim)
                    out.append(h["name"], dim).append(f"  {ago(h.get('last')).replace(' ago', '')}\n", dim)
        self.query_one("#nicklist", Static).update(out)

    def map_key(self, key: str) -> None:
        """Pan and zoom the map from the keyboard (only while the map is showing and the input is empty)."""
        from .views import MapState

        if not hasattr(self, "map_state"):
            self.map_state = MapState(self)
        ms = self.map_state
        moves = {"left": (-0.25, 0), "right": (0.25, 0), "up": (0, -0.25), "down": (0, 0.25)}
        if key in moves:
            ms.pan(*moves[key])
        elif key in ("+", "="):
            ms.zoom(2)
        elif key in ("-", "_"):
            ms.zoom(0.5)
        elif key == "0":
            ms.fit()
        elif key == "c" and self.my_pos:
            ms.center(*self.my_pos)
        self.refresh_view()

    def refresh_view(self) -> None:
        w = self.win
        if w.kind != "view":
            return
        from . import views

        view = self.query_one("#view", Static)
        size = view.content_region
        view.update(getattr(views, f"render_{w.view}")(self, max(size.width, 40), max(size.height, 10)))

    # ── window management ─────────────────────────────────────────────────
    def switch(self, idx: int) -> None:
        if 0 <= idx < len(self.windows):
            old = self.win
            if old.recs and idx != self.current:
                old.marker = old.recs[-1].get("id")
            self.current = idx
            self.win.activity = 0
            self.redraw()
            self.refresh_chrome()

    def action_goto(self, n: int) -> None:
        self.switch(n - 1)

    def action_cycle(self, step: int) -> None:
        self.switch((self.current + step) % len(self.windows))

    def action_next_active(self) -> None:
        active = sorted(((w.activity, i) for i, w in enumerate(self.windows) if w.activity), reverse=True)
        if active:
            self.switch(active[0][1])

    def action_scroll(self, direction: int) -> None:
        log = self.query_one("#log", RichLog)
        (log.scroll_page_down if direction > 0 else log.scroll_page_up)(animate=False)

    def action_toggle_nicklist(self) -> None:
        nl = self.query_one("#nicklist")
        nl.display = not nl.display
        self.call_after_refresh(self.redraw)

    def find_window(self, key: str) -> Window | None:
        return next((w for w in self.windows if w.key == key), None)

    def open_window(self, win: Window) -> Window:
        if existing := self.find_window(win.key):
            return existing
        if self.store and win.kind in ("channel", "query"):
            win.recs = self.store.load(win.key)
            for rec in win.recs:
                if win.kind == "channel" and rec.get("k") == "msg" and not rec.get("own"):
                    win.speakers[rec["nick"]] = rec["t"]
        # channels sort by slot after the status window; everything else goes at the end
        if win.kind == "channel":
            pos = 1 + sum(1 for w in self.windows if w.kind == "channel" and w.channel_idx < win.channel_idx)
            self.windows.insert(pos, win)
            if pos <= self.current and len(self.windows) > 1:
                self.current += 1
        else:
            self.windows.append(win)
        self.save_state()
        self.refresh_statusbar()
        return win

    def close_window(self, win: Window) -> None:
        if win.kind == "status":
            return
        idx = self.windows.index(win)
        self.windows.remove(win)
        if self.split_win is win:
            self.split_win = None
        if self.current >= idx:
            self.current = max(0, self.current - 1)
        self.save_state()
        self.switch(self.current)

    def query_window(self, contact: dict | None, prefix: str = "") -> Window:
        if contact:
            return self.open_window(Window("query", f"dm:{contact['public_key']}", contact["adv_name"], pubkey=contact["public_key"]))
        return self.open_window(Window("query", f"dm:{prefix}", self.name_for(prefix) or prefix, pubkey=prefix))

    def special_window(self, kind: str, view: str | None = None) -> Window:
        name = f"({view or kind})"
        win = self.open_window(Window(kind, name, name, view=view))
        if kind == "rf" and not win.recs:
            for s in list(self.rf)[-500:]:
                win.recs.append({"k": "rf", "s": s, "t": s["t"], "id": uuid.uuid4().hex[:10]})
        return win

    def save_state(self) -> None:
        if self.store:
            state = self.store.load_state()
            state["queries"] = [w.pubkey for w in self.windows if w.kind == "query"]
            state["specials"] = [w.view or w.kind for w in self.windows if w.kind in ("rf", "view")]
            state["dash_watch"] = list(self.dash)
            self.store.save_state(state)

    # ── contacts & name helpers ───────────────────────────────────────────
    def contact(self, key: str | None) -> dict | None:
        if not key or not self.mc:
            return None
        return self.mc.contacts.get(key) or self.mc.get_contact_by_key_prefix(key)

    def name_for(self, key_prefix: str) -> str | None:
        """Best-known name for a key prefix: contacts first, then anything heard over the air."""
        if not key_prefix:
            return None
        if self.self_info.get("public_key", "").startswith(key_prefix):
            return self.my_name
        if c := self.contact(key_prefix):
            return c["adv_name"]
        for k, h in self.heard.items():
            if k.startswith(key_prefix):
                return h["name"]
        return None

    def resolve_hash(self, h: str) -> str | None:
        """Path hops are the first 1–2 bytes of a repeater's key; name it if that's unambiguous."""
        if not h:
            return None
        pool = {}
        for k, c in (self.mc.contacts.items() if self.mc else []):
            if k.startswith(h) and c["type"] in (2, 3):
                pool[k] = c["adv_name"]
        for k, n in self.heard.items():
            if k.startswith(h) and n.get("type") in (2, 3):
                pool.setdefault(k, n["name"])
        return next(iter(pool.values())) if len(pool) == 1 else None

    def path_str(self, c: dict) -> str:
        n = c.get("out_path_len", -1)
        if n < 0 or n == 255:
            return "flood"
        if n == 0:
            return "direct (0 hops)"
        width = (c.get("out_path_hash_mode", 0) + 1) * 2
        p = c.get("out_path", "")
        hops = [p[i : i + width] for i in range(0, len(p), width)]
        return f"{n} hop{'s' if n > 1 else ''} via " + ",".join(self.resolve_hash(h) or h for h in hops)

    def find_contact(self, query: str) -> dict | None:
        if not self.mc:
            return None
        q = query.strip().lower()
        if not q:
            return None
        cs = list(self.mc.contacts.values())
        q = " ".join(q.strip(":").split())
        for test in (  # most to least specific; emoji in names can be skipped or typed by name (🐢 = turtle)
            lambda c: c["adv_name"].lower() == q or q in name_forms(c["adv_name"]),
            lambda c: c["public_key"].startswith(q) and len(q) >= 4,
            lambda c: any(f.startswith(q) for f in name_forms(c["adv_name"])),
            lambda c: name_matches(c["adv_name"], q),
            lambda c: any(q in f for f in name_forms(c["adv_name"])),
        ):
            hits = [c for c in cs if test(c)]
            if len(hits) == 1:
                return hits[0]
            if len(hits) > 1:
                return None
        return None

    def split_target(self, args: str) -> tuple[dict | None, str]:
        """Split '<contact> rest' where contact names may contain spaces."""
        if not self.mc:
            return None, args
        low = args.lower()
        best = None  # longest spelling of a name (with or without its emoji) that the arguments start with
        for c in self.mc.contacts.values():
            for form in name_forms(c["adv_name"]):
                if low.startswith(form) and (len(low) == len(form) or low[len(form)] == " "):
                    if best is None or len(form) > best[1]:
                        best = (c, len(form))
        if best:
            return best[0], args[best[1] :].strip()
        if args.startswith('"') and '"' in args[1:]:
            end = args.index('"', 1)
            return self.find_contact(args[1:end]), args[end + 1 :].strip()
        head, _, rest = args.partition(" ")
        return self.find_contact(head), rest.strip()

    def completions(self, before: str) -> tuple[int, list[str], int]:
        if before.startswith("/") and " " not in before:
            names = sorted(c for c in self.command_names() if c.startswith(before[1:].lower()))
            return 1, [n + " " for n in names], 0
        if before.lower().startswith("/set ") and " " not in before[5:]:
            keys = sorted(f"{s}.{k}" for s, vals in self.cfg.data.items() for k in vals)
            return 5, [k + " " for k in keys if k.startswith(before[5:])], 0
        if before.lower().startswith("/theme "):
            return 7, [n for n in sorted(THEMES) if n.startswith(before[7:])], 0
        # complete the longest trailing fragment that prefixes a known name (names can contain spaces)
        names = {h["name"] for h in self.heard.values() if h.get("name")}
        if self.mc:
            names |= {c["adv_name"] for c in self.mc.contacts.values()}
        names |= set(self.win.speakers)
        names |= {w.name for w in self.windows if w.kind == "channel"}
        at_start = not before.startswith("/")
        for start in range(len(before) + 1):
            if start and before[start - 1] != " ":
                continue
            frag = before[start:].lower()
            if not frag and start != len(before):
                continue
            hits = sorted((n for n in names if name_matches(n, frag)),
                          key=lambda n: (not n.lower().startswith(frag), n.lower()))  # plain prefix matches first
            if hits:
                if self.win.kind == "channel" and at_start and start == 0:
                    return start, [f"@[{h}] " for h in hits], 0
                return start, [h + " " for h in hits], 0
        return 0, [], 0

    # ── connection & device events ────────────────────────────────────────
    async def open_radio(self) -> MeshCore | None:
        if self.target.startswith("ble"):
            address = self.target.partition(":")[2] or None
            return await MeshCore.create_ble(address, pin=self.cfg.get("connection.ble_pin") or None,
                                             auto_reconnect=True, max_reconnect_attempts=1000)
        if self.target.startswith("/dev/") or self.target.upper().startswith("COM"):
            return await MeshCore.create_serial(self.target, self.baud, auto_reconnect=True, max_reconnect_attempts=1000)
        host, _, port = self.target.partition(":")
        return await MeshCore.create_tcp(host, int(port or 5000), auto_reconnect=True, max_reconnect_attempts=1000)

    async def connect(self) -> None:
        self.status(f"Connecting to {self.target}…")
        try:
            self.mc = await self.open_radio()
        except Exception as e:  # noqa: BLE001
            self.mc = None
            self.status(f"Connection failed: {e}", "error")
        if not self.mc:
            self.status("No response from radio. Is another app connected to it? /reconnect to retry "
                        "(or share the radio between apps with `meshssi --serve`).", "error")
            return
        mc = self.mc
        mc.set_decrypt_channel_logs(True)
        for etype, handler in (
            (EventType.CONTACT_MSG_RECV, self.on_contact_msg),
            (EventType.CHANNEL_MSG_RECV, self.on_channel_msg),
            (EventType.ACK, self.on_ack),
            (EventType.ADVERTISEMENT, self.on_advert),
            (EventType.NEW_CONTACT, self.on_new_contact),
            (EventType.PATH_UPDATE, self.on_path_update),
            (EventType.RX_LOG_DATA, self.on_rx_log),
            (EventType.DISCONNECTED, self.on_disconnected),
            (EventType.CONNECTED, self.on_reconnected),
            (EventType.SELF_INFO, self.on_self_info),
            (EventType.CONTACT_DELETED, self.on_contact_deleted),
            (EventType.CONTACTS_FULL, self.on_contacts_full),
        ):
            mc.subscribe(etype, handler)
        mc.subscribe(EventType.MESSAGES_WAITING, lambda e: self.fetch_messages())
        await self.on_connected()

    async def on_connected(self) -> None:
        mc = self.mc
        self.connected = True
        self.drops.clear()
        self.self_info = dict(mc.self_info)
        self.store = self.make_store(self.self_info["public_key"])
        async with self.io:
            ev = await mc.commands.send_device_query()
            if not ev.is_error():
                self.device_info = ev.payload
        await self.refresh_contacts()
        state = self.store.load_state()
        for k, v in state.get("heard", {}).items():
            self.heard.setdefault(k, v)
        for c in mc.contacts.values():
            self.note_heard(c["public_key"], c["adv_name"], c["type"], c.get("adv_lat"), c.get("adv_lon"),
                            c.get("last_advert"))
        self.status(f"Connected: {self.my_name} ({self.self_info['public_key'][:12]}) · "
                    f"{self.device_info.get('model', '?')} {self.device_info.get('ver', '')}", "ok")
        await self.load_channels()
        for key in state.get("queries", []):
            self.query_window(self.contact(key), key)
        for special in state.get("specials", []):
            self.special_window("rf") if special == "rf" else self.special_window("view", special)
        watch = list(state.get("dash_watch", []))
        for name in self.cfg.get("dashboard.watch", []):
            if c := self.find_contact(name):
                watch.append(c["public_key"])
        for key in watch:
            self.dash.setdefault(key, (0, None))
        await self.check_clock()
        await self.poll_stats()
        self.fetch_messages()
        self.switch(self.current)
        self.run_worker(self.auto_login_rooms(), group="rooms")
        self.plugins.emit("connect")

    def make_store(self, node_key: str) -> Store:
        return Store(node_key)

    async def refresh_contacts(self) -> None:
        """Fetch contacts, subscribing before sending (meshcore's get_contacts can miss a fast reply)."""
        async with self.io:
            waiter = asyncio.create_task(self.mc.wait_for_event(EventType.CONTACTS, timeout=15))
            await asyncio.sleep(0)
            await self.mc.commands.get_contacts_async()
            ev = await waiter
        if ev is None:
            raise RuntimeError("timed out fetching contacts")

    def schedule_contacts_refresh(self) -> None:
        """Debounced contact reload after adverts/path changes (bursts of adverts arrive together)."""
        if self._contacts_refresh and not self._contacts_refresh.done():
            return

        async def later():
            await asyncio.sleep(1.5)
            if self.connected and self.mc:
                try:
                    await self.refresh_contacts()
                except RuntimeError:
                    return
                self.refresh_nicklist()
                self.refresh_topic()

        self._contacts_refresh = asyncio.create_task(later())

    async def load_channels(self) -> None:
        self.channels.clear()
        for idx in range(self.device_info.get("max_channels", 8)):
            async with self.io:
                ev = await self.mc.commands.get_channel(idx)
            if ev.is_error():
                break
            ch = ev.payload
            if ch.get("channel_name"):
                self.channels[idx] = ch
                self.open_window(Window("channel", f"chan:{ch['channel_name']}", ch["channel_name"], channel_idx=idx))
        # drop windows for channels that vanished
        for w in [w for w in self.windows if w.kind == "channel" and w.channel_idx not in self.channels]:
            self.windows.remove(w)
        self.current = min(self.current, len(self.windows) - 1)

    async def check_clock(self) -> None:
        async with self.io:
            ev = await self.mc.commands.get_time()
        if ev.is_error():
            return
        drift = ev.payload["time"] - time.time()
        if abs(drift) > 10:
            if self.cfg.get("device.auto_time_sync", True):
                async with self.io:
                    await self.mc.commands.set_time(int(time.time()))
                self.status(f"Radio clock was off by {drift:+.0f}s — synced from this computer.", "ok")
            else:
                self.status(f"Device clock is off by {drift:+.0f}s — run /time sync", "error")

    async def poll_stats(self) -> None:
        if not self.mc or not self.connected:
            return
        sample = {"t": time.time()}
        async with self.io:
            for cmd in (self.mc.commands.get_stats_core, self.mc.commands.get_stats_radio, self.mc.commands.get_stats_packets):
                ev = await cmd()
                if not ev.is_error():
                    self.stats.update(ev.payload)
                    sample.update(ev.payload)
        if len(sample) > 1:
            self.samples.append(sample)
        self.refresh_statusbar()

    async def periodic(self) -> None:
        """Once a minute: automatic adverts, repeater dashboard polls, saving what we've heard."""
        if not self.connected:
            return
        every = int(self.cfg.get("device.advert_interval", 0) or 0)
        if every and time.time() - self.last_advert >= every * 60:
            async with self.io:
                await self.mc.commands.send_advert(flood=bool(self.cfg.get("device.advert_flood")))
            self.last_advert = time.time()
            self.add(self.windows[0], {"k": "notice", "text": "Sent scheduled advert.", "lvl": "dim"}, activity=0)
        interval = float(self.cfg.get("dashboard.interval", 10)) * 60
        for key, (t, _) in list(self.dash.items()):
            if time.time() - t >= interval:
                self.run_worker(self.poll_repeater(key), group=f"dash-{key}")
        self.save_heard()

    def save_heard(self) -> None:
        if self.store:
            state = self.store.load_state()
            state["heard"] = dict(sorted(self.heard.items(), key=lambda kv: -kv[1].get("last", 0))[:500])
            self.store.save_state(state)

    async def poll_repeater(self, key: str) -> None:
        c = self.contact(key)
        if not c:
            return
        self.dash[key] = (time.time(), self.dash.get(key, (0, None))[1])
        st = await self.mc.commands.req_status_sync(c)
        self.dash[key] = (time.time(), st if st else self.dash[key][1])
        if st is None:
            self.add(self.windows[0], {"k": "notice", "text": f"(dash) no status reply from {c['adv_name']}",
                                       "lvl": "dim"}, activity=0)

    async def auto_login_rooms(self) -> None:
        for name, pwd in (self.cfg["rooms"] or {}).items():
            c = self.find_contact(name)
            if not c:
                continue
            ev = await self.mc.commands.send_login_sync(c, str(pwd))
            ok = ev is not None and ev.type == EventType.LOGIN_SUCCESS
            self.status(f"{'Logged into' if ok else 'Could not log into'} room {c['adv_name']}", "ok" if ok else "error",
                        win=self.query_window(c))

    def fetch_messages(self) -> None:
        self.run_worker(self._fetch_messages(), group="fetch", exclusive=True)

    async def _fetch_messages(self) -> None:
        while self.mc and self.connected:
            async with self.io:
                ev = await self.mc.commands.get_msg()
            if ev.type in (EventType.NO_MORE_MSGS, EventType.ERROR):
                return

    def is_ignored(self, nick: str) -> bool:
        return any(fnmatch.fnmatch(nick.lower(), pat.lower()) for pat in self.cfg.get("chat.ignores", []))

    def is_hilight(self, text: str) -> bool:
        low = text.lower()
        words = [self.my_name.lower()] + [w.lower() for w in self.cfg.get("chat.highlights", [])]
        return any(w and w in low for w in words)

    def note_snr(self, name: str, snr) -> None:
        if snr is not None:
            self.snr_hist.setdefault(name, collections.deque(maxlen=200)).append((time.time(), snr))

    def note_heard(self, key: str, name: str, type_: int, lat=None, lon=None, last=None, **extra) -> None:
        h = self.heard.setdefault(key, {})
        h.update(name=name, type=type_, last=last or time.time(), **extra)
        if geo.has_fix(lat, lon):
            h.update(lat=lat, lon=lon)

    async def on_contact_msg(self, ev) -> None:
        p = ev.payload
        c = self.contact(p["pubkey_prefix"])
        nick = c["adv_name"] if c else (self.name_for(p["pubkey_prefix"]) or p["pubkey_prefix"])
        if self.is_ignored(nick):
            self.ignored += 1
            return
        win = self.query_window(c, p["pubkey_prefix"])
        if p.get("txt_type") == 1:  # CLI reply from a repeater/room we sent a command to
            self.add(win, {"k": "reply", "nick": nick, "text": p["text"]}, activity=2)
            return
        if p.get("txt_type") == 2 and p.get("signature"):  # room server relaying someone else's post
            nick = self.name_for(p["signature"]) or p["signature"]
            if nick == self.my_name:
                return  # our own post echoed back by the room
        rec = {"k": "msg", "nick": nick, "text": p["text"], "hops": p.get("path_len"),
               "snr": p.get("SNR"), "st_ts": p.get("sender_timestamp")}
        self.note_snr(nick, p.get("SNR"))
        self.add(win, rec, activity=3)
        self.alert(win, f"{nick} (DM)", p["text"])
        if win is self.win and win.kind == "query":
            self.refresh_nicklist()
        self.plugins.emit("dm", win=win, rec=rec, contact=c)
        if self.away is not None and c and c["type"] == 1 and c["public_key"] not in self.away_replied:
            self.away_replied.add(c["public_key"])
            await self.say(win, f"[away] {self.away or 'not here right now'}")

    async def on_channel_msg(self, ev) -> None:
        p = ev.payload
        idx = p["channel_idx"]
        if idx not in self.channels:
            await self.load_channels()
        win = next((w for w in self.windows if w.kind == "channel" and w.channel_idx == idx), None)
        if win is None:
            self.status(f"Message on unknown channel slot {idx}: {p['text']}")
            return
        nick, sep, text = p["text"].partition(": ")
        if not sep:
            nick, text = "?", p["text"]
        if self.is_ignored(nick):
            self.ignored += 1
            return
        hl = self.is_hilight(text)
        win.speakers[nick] = time.time()
        path_names = None
        if p.get("path"):
            mode = p.get("path_hash_mode", 0)
            hops = packets.split_path(p["path"], mode + 1 if mode >= 0 else 1)
            path_names = ",".join(self.resolve_hash(h) or h for h in hops)
        rec = {"k": "msg", "nick": nick, "text": text, "hl": hl, "hops": p.get("path_len"),
               "snr": p.get("SNR"), "st_ts": p.get("sender_timestamp"), "path": path_names}
        self.note_snr(nick, p.get("SNR"))
        self.add(win, rec, activity=3 if hl else 2)
        if hl:
            self.alert(win, f"{nick} in {win.name}", text)
        if win is self.win:
            self.refresh_nicklist()
            self.refresh_topic()
        self.plugins.emit("channel_message", win=win, rec=rec)

    async def on_rx_log(self, ev) -> None:
        p = ev.payload
        if "route_typename" not in p:
            return
        s = packets.summarize(p, self.resolve_hash)
        self.rf.append(s)
        if s["ptype"] == "ADVERT" and s.get("adv_key") and s.get("adv_name"):
            self.note_heard(s["adv_key"], s["adv_name"], s.get("adv_type", 0), s.get("adv_lat"), s.get("adv_lon"),
                            snr=s.get("snr"), rssi=s.get("rssi"), hops=len(s["hops"]))
            self.note_snr(s["adv_name"], s.get("snr"))
            self.plugins.emit("advert", node=self.heard[s["adv_key"]], packet=s)
        elif s["ptype"] == "GRP_TXT" and s.get("message", "").startswith(self.my_name + ": "):
            self.count_heard(s)
        if rf := self.find_window("(rf)"):
            self.add(rf, {"k": "rf", "s": s, "t": s["t"]}, persist=False, activity=0)
        self.plugins.emit("packet", packet=s)

    def count_heard(self, s: dict) -> None:
        """Our own channel message relayed back to us by a repeater: bump its heard-by count."""
        text = s["message"][len(self.my_name) + 2 :]
        for win, rec in reversed(self.sent_chan):
            if rec["text"] == text and s.get("sender_timestamp") in (None, rec.get("st_ts")):
                rec["heard"] = rec.get("heard", 0) + 1
                if s["hops"]:
                    rec.setdefault("heard_via", []).append(s["hop_names"][-1] or s["hops"][-1])
                if win is self.win:
                    self.redraw()
                return

    async def on_ack(self, ev) -> None:
        entry = self.acks.pop(ev.payload.get("code", ""), None)
        if entry:
            win, rec, fut = entry
            if not fut.done():
                fut.set_result(ev.payload.get("trip_time"))
            else:  # a late ack after we'd given up: it did get there
                self.set_delivery(win, rec, "ok")

    def set_delivery(self, win: Window, rec: dict, st: str) -> None:
        if rec.get("st") == st:
            return
        rec["st"] = st
        if self.store:
            self.store.append(win.key, {"k": "ack", "ref": rec["id"], "st": st})
        if win is self.win:
            self.redraw()

    async def on_advert(self, ev) -> None:
        key = ev.payload["public_key"]
        name = self.name_for(key) or key[:12]
        self.add(self.windows[0], {"k": "notice", "text": f"Advert from {name}", "lvl": "dim"}, activity=0)
        self.schedule_contacts_refresh()

    async def on_new_contact(self, ev) -> None:
        c = ev.payload
        self.note_heard(c["public_key"], c["adv_name"], c["type"], c.get("adv_lat"), c.get("adv_lon"), c.get("last_advert"))
        self.status(f"New node heard: {c['adv_name']} ({CONTACT_TYPES.get(c['type'], '?')}, "
                    f"{c['public_key'][:12]}) — /accept {c['adv_name']}", "join")

    async def on_path_update(self, ev) -> None:
        key = ev.payload["public_key"]
        try:
            await self.refresh_contacts()
        except RuntimeError:
            return
        if c := self.contact(key):
            self.add(self.windows[0], {"k": "notice", "text": f"Route to {c['adv_name']} is now: {self.path_str(c)}",
                                       "lvl": "dim"}, activity=0)
            if self.win.kind == "query":
                self.refresh_topic()
                self.refresh_nicklist()

    async def on_contact_deleted(self, ev) -> None:
        key = ev.payload.get("public_key", "")
        name = self.name_for(key) or key[:12]
        if self.mc:
            self.mc.contacts.pop(key, None)
        self.status(f"Radio dropped contact {name} (table full, or deleted elsewhere).", "dim")
        self.refresh_nicklist()

    async def on_contacts_full(self, ev) -> None:
        self.status("The radio's contact table is full — /rmcontact some old nodes.", "error")

    async def on_self_info(self, ev) -> None:
        self.self_info = dict(ev.payload)
        self.refresh_chrome()

    async def on_disconnected(self, ev) -> None:
        if ev.payload.get("reason") == "manual_disconnect":
            return
        now = time.time()
        self.drops = [t for t in self.drops if now - t < 60] + [now]
        if len(self.drops) >= 3 and self.mc:
            # WiFi companions serve one client at a time; a newcomer kicks the old one off
            self.connected = False
            mc, self.mc = self.mc, None
            self.run_worker(mc.disconnect())
            self.status("The radio keeps dropping us — another client (phone app, Home Assistant…) is probably "
                        "connected to it. Disconnect that (or share the radio with `meshssi --serve`), then /reconnect.", "error")
            self.refresh_chrome()
            return
        if self.connected:
            self.connected = False
            self.status(f"Disconnected ({ev.payload.get('reason', 'lost')}); reconnecting…", "error")
            self.refresh_chrome()

    async def on_reconnected(self, ev) -> None:
        if not self.connected and self.mc:
            self.status("Reconnected.", "ok")
            await self.on_connected()

    async def action_quit(self) -> None:
        self.save_heard()
        if self.mc:
            try:
                await asyncio.wait_for(self.mc.disconnect(), 2)
            except Exception:  # noqa: BLE001
                pass
        self.exit()

    # ── input & sending ───────────────────────────────────────────────────
    async def on_input_submitted(self, event: Input.Submitted) -> None:
        line = event.value
        inp = self.query_one("#input", PromptInput)
        inp.remember(line)
        inp.value = ""
        if not line.strip():
            return
        if line.startswith("/") and not line.startswith("//"):
            await self.run_command(line[1:])
        else:
            await self.say(self.win, line[1:] if line.startswith("//") else line)

    async def say(self, win: Window, text: str) -> None:
        if win.kind not in ("channel", "query"):
            self.echo("Not a chat window. Use /join, /query or switch windows (alt+N, ctrl+n/p).", "error")
            return
        if not self.connected:
            self.echo("Not connected.", "error")
            return
        if self.cfg.get("chat.emoji_shortcodes", True):
            text = expand_shortcodes(text)
        if win.kind == "channel":
            limit = MAX_TEXT - len(self.my_name.encode()) - 2
            for chunk in split_utf8(text, limit):
                ts = int(time.time())
                async with self.io:
                    ev = await self.mc.commands.send_chan_msg(win.channel_idx, chunk, timestamp=ts)
                if ev.is_error():
                    self.echo(f"Send failed: {ev.payload}", "error")
                    return
                rec = self.add(win, {"k": "msg", "nick": self.my_name, "text": chunk, "own": True, "st_ts": ts})
                self.sent_chan.append((win, rec))
                self.plugins.emit("sent", win=win, rec=rec)
            return
        c = self.contact(win.pubkey)
        if not c:
            self.echo("That contact isn't in the radio's contact list — can't encrypt to it.", "error")
            return
        recs = [self.add(win, {"k": "msg", "nick": self.my_name, "text": chunk, "own": True, "st": "pending"})
                for chunk in split_utf8(text, MAX_TEXT)]
        self.run_worker(self._deliver(win, c, recs), group=f"dm-{c['public_key']}")

    async def _deliver(self, win: Window, c: dict, recs: list[dict]) -> None:
        """Send DMs in order, retrying on missing acks and falling back to flood routing."""
        retries = max(1, int(self.cfg.get("chat.dm_retries", 3)))
        flood_after = int(self.cfg.get("chat.flood_after", 2))
        for rec in recs:
            fut = asyncio.get_running_loop().create_future()
            ts = int(time.time())
            for attempt in range(retries):
                if attempt and attempt == flood_after and c.get("out_path_len", -1) >= 0:
                    async with self.io:
                        await self.mc.commands.reset_path(c)
                    self.status(f"No ack from {c['adv_name']} on its stored route; flooding instead.", "dim", win=win)
                async with self.io:
                    ev = await self.mc.commands.send_msg(c, rec["text"], timestamp=ts, attempt=attempt)
                if ev.is_error():
                    self.set_delivery(win, rec, "fail")
                    self.echo(f"Send failed: {ev.payload}", "error")
                    return
                self.acks[ev.payload["expected_ack"].hex()] = (win, rec, fut)
                if attempt:
                    rec["try"] = attempt + 1
                    if win is self.win:
                        self.redraw()
                timeout = max(ev.payload.get("suggested_timeout", 8000) / 1000 * 1.3, 6)
                done, _ = await asyncio.wait({fut}, timeout=timeout)
                if done:
                    rec["rtt"] = fut.result()
                    self.set_delivery(win, rec, "ok")
                    self.plugins.emit("sent", win=win, rec=rec)
                    break
            else:
                self.set_delivery(win, rec, "fail")
