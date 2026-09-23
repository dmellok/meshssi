"""The TUI: irssi-style windows over a MeshCore companion connection."""

import asyncio
import time
import uuid
import zlib
from dataclasses import dataclass, field

from meshcore import EventType, MeshCore
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Input, RichLog, Static

from .commands import CommandsMixin
from .store import Store

NICK_COLORS = [
    "cyan", "green", "yellow", "magenta", "bright_blue", "bright_cyan", "bright_green",
    "bright_yellow", "bright_magenta", "orange1", "orchid", "spring_green2", "deep_sky_blue1",
    "light_salmon1", "khaki1", "plum1",
]
CONTACT_TYPES = {0: "?", 1: "chat", 2: "repeater", 3: "room", 4: "sensor"}
TYPE_GLYPH = {1: "@", 2: "R", 3: "#", 4: "S"}
MAX_TEXT = 150  # bytes per mesh text packet, leaving headroom under the firmware's 160


def nick_color(nick: str) -> str:
    return NICK_COLORS[zlib.crc32(nick.encode()) % len(NICK_COLORS)]


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


@dataclass
class Window:
    kind: str  # "status" | "channel" | "query"
    key: str
    name: str
    channel_idx: int | None = None
    pubkey: str | None = None
    recs: list[dict] = field(default_factory=list)
    activity: int = 0  # 0 none, 1 events, 2 messages, 3 hilight/DM
    speakers: dict[str, float] = field(default_factory=dict)


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
        if not self.history:
            return
        self.hist_pos = max(0, min(len(self.history), self.hist_pos + step))
        self.value = self.history[self.hist_pos] if self.hist_pos < len(self.history) else ""
        self.cursor_position = len(self.value)

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
    Screen { background: #000000; }
    #topic { height: 1; background: #00005f; color: #ffffff; padding: 0 1; }
    #main { height: 1fr; }
    #log { background: #000000; border: none; padding: 0 1; scrollbar-size-vertical: 1; }
    #nicklist { width: 32; background: #0a0a0a; border-left: solid #303030; padding: 0 1; }
    #statusbar { height: 1; background: #00005f; color: #ffffff; padding: 0 1; }
    #promptrow { height: 1; }
    #prompt { width: auto; color: #ffffff; padding: 0 0 0 1; }
    #input { border: none; height: 1; padding: 0 1; background: #000000; }
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

    def __init__(self, target: str, baud: int = 115200):
        super().__init__()
        self.target = target
        self.baud = baud
        self.mc: MeshCore | None = None
        self.io = asyncio.Lock()  # serialize request/response commands to the radio
        self.store: Store | None = None
        self.windows: list[Window] = [Window("status", "status", "(status)")]
        self.current = 0
        self.self_info: dict = {}
        self.device_info: dict = {}
        self.channels: dict[int, dict] = {}
        self.stats: dict = {}
        self.pending_acks: dict[str, tuple[Window, dict]] = {}
        self.pending_confirm: tuple[str, float] | None = None
        self.connected = False
        self.drops: list[float] = []  # recent disconnect times, to spot another client fighting for the radio

    # ── layout ────────────────────────────────────────────────────────────
    def compose(self) -> ComposeResult:
        yield Static(id="topic")
        with Horizontal(id="main"):
            yield RichLog(id="log", wrap=True, markup=False, highlight=False, max_lines=5000)
            yield Static(id="nicklist")
        yield Static(id="statusbar")
        with Horizontal(id="promptrow"):
            yield Static(id="prompt")
            yield PromptInput(id="input")

    def on_mount(self) -> None:
        self.query_one("#input").focus()
        self.status("meshssi — an irssi-style MeshCore client. /help for commands.")
        self.refresh_chrome()
        self.set_interval(1, self.refresh_statusbar)
        self.set_interval(60, self.poll_stats)
        self.run_worker(self.connect(), exclusive=True, group="connect")

    @property
    def win(self) -> Window:
        return self.windows[self.current]

    @property
    def my_name(self) -> str:
        return self.self_info.get("name", "me")

    # ── output ────────────────────────────────────────────────────────────
    def render_rec(self, rec: dict) -> Text:
        ts = time.strftime("%H:%M", time.localtime(rec.get("t", time.time())))
        line = Text(f"{ts} ", style="grey50")
        kind = rec.get("k")
        if kind == "msg":
            nick = rec.get("nick", "?")
            if rec.get("own"):
                line.append("<", "grey50").append(nick, "bold white").append("> ", "grey50")
            elif rec.get("hl"):
                line.append("<", "grey50").append(nick, "bold black on magenta").append("> ", "grey50")
            else:
                line.append("<", "grey50").append(nick, nick_color(nick)).append("> ", "grey50")
            self._append_body(line, rec.get("text", ""), "bold magenta" if rec.get("hl") else "")
            st = rec.get("st")
            if st == "pending":
                line.append(" …", "grey50")
            elif st == "ok":
                line.append(" ✓", "green")
            elif st == "fail":
                line.append(" ✗", "red")
            meta = []
            if rec.get("hops") is not None:
                meta.append("direct" if rec["hops"] in (0, 255) else f"{rec['hops']} hop{'s' if rec['hops'] > 1 else ''}")
            if rec.get("snr") is not None:
                meta.append(f"{rec['snr']:+.1f}dB")
            if meta:
                line.append(f"  [{' '.join(meta)}]", "grey35")
        elif kind == "reply":  # repeater / room CLI output
            line.append("-", "blue").append(rec.get("nick", "?"), "bold cyan").append("- ", "blue")
            line.append(rec.get("text", ""))
        else:
            lvl = rec.get("lvl", "info")
            marker, style = {
                "info": ("-!- ", "bold blue"),
                "error": ("-!- ", "bold red"),
                "ok": ("-!- ", "bold green"),
                "join": ("-!- ", "bold green"),
                "dim": ("--- ", "grey42"),
            }.get(lvl, ("-!- ", "bold blue"))
            line.append(marker, style)
            body = Text.from_markup(rec.get("text", "")) if rec.get("markup") else Text(rec.get("text", ""))
            body.stylize("red" if lvl == "error" else ("grey50" if lvl == "dim" else ""))
            line.append(body)
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
            line.append("@" + name, "bold " + ("reverse " if name == self.my_name else "") + nick_color(name))
            i = k + 1

    def add(self, win: Window, rec: dict, persist: bool = True, activity: int = 1) -> dict:
        rec.setdefault("t", time.time())
        rec.setdefault("id", uuid.uuid4().hex[:10])
        win.recs.append(rec)
        del win.recs[:-2000]
        if persist and self.store:
            self.store.append(win.key, rec)
        if win is self.win:
            self._write(rec)
        elif activity > win.activity:
            win.activity = activity
            self.refresh_statusbar()
        return rec

    def status(self, text: str, lvl: str = "info", markup: bool = False, win: Window | None = None) -> None:
        """Notice into a window (default: status). Status-window notices aren't persisted."""
        target = win or self.windows[0]
        self.add(target, {"k": "notice", "text": text, "lvl": lvl, "markup": markup},
                 persist=target.kind != "status", activity=1)

    def echo(self, text: str, lvl: str = "info", markup: bool = False) -> None:
        """Command output: into the current window, never persisted."""
        self.add(self.win, {"k": "notice", "text": text, "lvl": lvl, "markup": markup}, persist=False)

    def _write(self, rec: dict) -> None:
        log = self.query_one("#log", RichLog)
        # RichLog measures Text against the console width, not the pane, so give it the pane width
        width = log.scrollable_content_region.width
        log.write(self.render_rec(rec), width=width if width > 10 else None)

    def redraw(self) -> None:
        self.query_one("#log", RichLog).clear()
        for rec in self.win.recs[-1000:]:
            self._write(rec)
        self.query_one("#log", RichLog).scroll_end(animate=False)

    def on_resize(self) -> None:
        self.call_after_refresh(self.redraw)

    # ── chrome (topic, status bar, nicklist, prompt) ──────────────────────
    def refresh_chrome(self) -> None:
        self.refresh_topic()
        self.refresh_statusbar()
        self.refresh_nicklist()
        label = {"status": "[(status)]", "channel": f"[{self.win.name}]", "query": f"[{self.win.name}]"}[self.win.kind]
        self.query_one("#prompt", Static).update(Text(label, "bold white"))

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
        else:
            c = self.contact(w.pubkey)
            if c:
                path = self.path_str(c)
                t = f"{c['adv_name']} · {CONTACT_TYPES.get(c['type'], '?')} · {path} · advert {ago(c.get('last_advert'))} · {c['public_key'][:12]}"
            else:
                t = f"{w.name} · unknown contact {w.pubkey}"
        self.query_one("#topic", Static).update(Text(t, no_wrap=True, overflow="ellipsis"))

    def refresh_statusbar(self) -> None:
        bar = Text(no_wrap=True, overflow="ellipsis")
        br = ("[", "#5f87ff")
        er = ("] ", "#5f87ff")
        bar.append(*br).append(time.strftime("%H:%M")).append(*er)
        bar.append(*br).append(self.my_name, "bold")
        bar.append(f"({'+' if self.connected else 'offline'})", "green" if self.connected else "bold red").append(*er)
        bar.append(*br).append(f"{self.current + 1}:", "white").append(self.win.name, "bold").append(*er)
        act = [(i, w.activity) for i, w in enumerate(self.windows) if w.activity and i != self.current]
        if act:
            bar.append(*br).append("Act: ")
            for n, (i, a) in enumerate(act):
                if n:
                    bar.append(",")
                bar.append(str(i + 1), {1: "grey62", 2: "bold white", 3: "bold magenta"}[a])
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
        if w.kind == "channel":
            out.append(f"heard in {w.name}\n", "bold underline")
            for nick, ts in sorted(w.speakers.items(), key=lambda kv: -kv[1]):
                out.append(nick, nick_color(nick)).append(f"  {ago(ts)}\n", "grey42")
        elif w.kind == "query" and (c := self.contact(w.pubkey)):
            out.append(f"{c['adv_name']}\n", "bold underline")
            out.append(f"type   {CONTACT_TYPES.get(c['type'])}\n")
            out.append(f"key    {c['public_key'][:12]}\n")
            out.append(f"route  {self.path_str(c)}\n")
            out.append(f"advert {ago(c.get('last_advert'))}\n")
            if c.get("adv_lat") or c.get("adv_lon"):
                out.append(f"loc    {c['adv_lat']:.4f}\n       {c['adv_lon']:.4f}\n")
        else:
            contacts = sorted(self.mc.contacts.values() if self.mc else [], key=lambda c: -c.get("last_advert", 0))
            out.append(f"contacts ({len(contacts)})\n", "bold underline")
            for c in contacts:
                out.append(TYPE_GLYPH.get(c["type"], "?") + " ", "grey50")
                out.append(c["adv_name"], nick_color(c["adv_name"])).append(f"  {ago(c.get('last_advert'))}\n", "grey42")
        self.query_one("#nicklist", Static).update(out)

    # ── window management ─────────────────────────────────────────────────
    def switch(self, idx: int) -> None:
        if 0 <= idx < len(self.windows):
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
        if self.store:
            win.recs = self.store.load(win.key)
            for rec in win.recs:
                if win.kind == "channel" and rec.get("k") == "msg" and not rec.get("own"):
                    win.speakers[rec["nick"]] = rec["t"]
        # channels sort by slot after the status window; queries go at the end
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
        if self.current >= idx:
            self.current = max(0, self.current - 1)
        self.save_state()
        self.switch(self.current)

    def query_window(self, contact: dict | None, prefix: str = "") -> Window:
        if contact:
            return self.open_window(Window("query", f"dm:{contact['public_key']}", contact["adv_name"], pubkey=contact["public_key"]))
        return self.open_window(Window("query", f"dm:{prefix}", prefix, pubkey=prefix))

    def save_state(self) -> None:
        if self.store:
            self.store.save_state({"queries": [w.pubkey for w in self.windows if w.kind == "query"]})

    # ── contacts helpers ──────────────────────────────────────────────────
    def contact(self, key: str | None) -> dict | None:
        if not key or not self.mc:
            return None
        return self.mc.contacts.get(key) or self.mc.get_contact_by_key_prefix(key)

    @staticmethod
    def path_str(c: dict) -> str:
        n = c.get("out_path_len", -1)
        if n < 0 or n == 255:
            return "flood"
        if n == 0:
            return "direct (0 hops)"
        width = (c.get("out_path_hash_mode", 0) + 1) * 2
        p = c.get("out_path", "")
        return f"{n} hops via " + ",".join(p[i : i + width] for i in range(0, len(p), width))

    def find_contact(self, query: str) -> dict | None:
        if not self.mc:
            return None
        q = query.strip().lower()
        cs = list(self.mc.contacts.values())
        for test in (
            lambda c: c["adv_name"].lower() == q,
            lambda c: c["public_key"].startswith(q) and len(q) >= 4,
            lambda c: c["adv_name"].lower().startswith(q),
            lambda c: q in c["adv_name"].lower(),
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
        for c in sorted(self.mc.contacts.values(), key=lambda c: -len(c["adv_name"])):
            n = c["adv_name"].lower()
            if low.startswith(n) and (len(low) == len(n) or low[len(n)] == " "):
                return c, args[len(n) :].strip()
        if args.startswith('"') and '"' in args[1:]:
            end = args.index('"', 1)
            return self.find_contact(args[1:end]), args[end + 1 :].strip()
        head, _, rest = args.partition(" ")
        return self.find_contact(head), rest.strip()

    def completions(self, before: str) -> tuple[int, list[str], int]:
        if before.startswith("/") and " " not in before:
            names = sorted(c for c in self.command_names() if c.startswith(before[1:].lower()))
            return 1, [n + " " for n in names], 0
        # complete the longest trailing fragment that prefixes a known name (names can contain spaces)
        names = set()
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
            hits = sorted(n for n in names if n.lower().startswith(frag))
            if hits:
                if self.win.kind == "channel" and at_start and start == 0:
                    return start, [f"@[{h}] " for h in hits], 0
                return start, [h + " " for h in hits], 0
        return 0, [], 0

    # ── connection & device events ────────────────────────────────────────
    async def connect(self) -> None:
        self.status(f"Connecting to {self.target}…")
        try:
            if self.target.startswith("/dev/") or self.target.upper().startswith("COM"):
                self.mc = await MeshCore.create_serial(self.target, self.baud, auto_reconnect=True, max_reconnect_attempts=1000)
            else:
                host, _, port = self.target.partition(":")
                self.mc = await MeshCore.create_tcp(host, int(port or 5000), auto_reconnect=True, max_reconnect_attempts=1000)
        except Exception as e:  # noqa: BLE001
            self.mc = None
            self.status(f"Connection failed: {e}", "error")
        if not self.mc:
            self.status("No response from radio. Is another app connected to it? /reconnect to retry.", "error")
            return
        mc = self.mc
        mc.subscribe(EventType.CONTACT_MSG_RECV, self.on_contact_msg)
        mc.subscribe(EventType.CHANNEL_MSG_RECV, self.on_channel_msg)
        mc.subscribe(EventType.MESSAGES_WAITING, lambda e: self.fetch_messages())
        mc.subscribe(EventType.ACK, self.on_ack)
        mc.subscribe(EventType.ADVERTISEMENT, self.on_advert)
        mc.subscribe(EventType.NEW_CONTACT, self.on_new_contact)
        mc.subscribe(EventType.PATH_UPDATE, self.on_path_update)
        mc.subscribe(EventType.DISCONNECTED, self.on_disconnected)
        mc.subscribe(EventType.CONNECTED, self.on_reconnected)
        mc.subscribe(EventType.SELF_INFO, self.on_self_info)
        await self.on_connected()

    async def on_connected(self) -> None:
        mc = self.mc
        self.connected = True
        self.self_info = dict(mc.self_info)
        self.store = Store(self.self_info["public_key"])
        async with self.io:
            ev = await mc.commands.send_device_query()
            if not ev.is_error():
                self.device_info = ev.payload
        await self.refresh_contacts()
        self.status(f"Connected: {self.my_name} ({self.self_info['public_key'][:12]}) · "
                    f"{self.device_info.get('model', '?')} {self.device_info.get('ver', '')}", "ok")
        saved_queries = self.store.load_state().get("queries", [])
        await self.load_channels()
        for key in saved_queries:
            c = self.contact(key)
            self.query_window(c, key)
        await self.check_clock()
        await self.poll_stats()
        self.fetch_messages()
        self.switch(self.current)

    async def refresh_contacts(self) -> None:
        """Fetch contacts, subscribing before sending (meshcore's get_contacts can miss a fast reply)."""
        async with self.io:
            waiter = asyncio.create_task(self.mc.wait_for_event(EventType.CONTACTS, timeout=15))
            await asyncio.sleep(0)
            await self.mc.commands.get_contacts_async()
            ev = await waiter
        if ev is None:
            raise RuntimeError("timed out fetching contacts")

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
        if not ev.is_error():
            drift = ev.payload["time"] - time.time()
            if abs(drift) > 30:
                self.status(f"Device clock is off by {drift:+.0f}s — run /time sync", "error")

    async def poll_stats(self) -> None:
        if not self.mc or not self.connected:
            return
        async with self.io:
            for cmd in (self.mc.commands.get_stats_core, self.mc.commands.get_stats_radio):
                ev = await cmd()
                if not ev.is_error():
                    self.stats.update(ev.payload)
        self.refresh_statusbar()

    def fetch_messages(self) -> None:
        self.run_worker(self._fetch_messages(), group="fetch", exclusive=True)

    async def _fetch_messages(self) -> None:
        while self.mc and self.connected:
            async with self.io:
                ev = await self.mc.commands.get_msg()
            if ev.type in (EventType.NO_MORE_MSGS, EventType.ERROR):
                return

    async def on_contact_msg(self, ev) -> None:
        p = ev.payload
        c = self.contact(p["pubkey_prefix"])
        win = self.query_window(c, p["pubkey_prefix"])
        nick = c["adv_name"] if c else p["pubkey_prefix"]
        if p.get("txt_type") == 1:  # CLI reply from a repeater/room we sent a command to
            self.add(win, {"k": "reply", "nick": nick, "text": p["text"]}, activity=2)
            return
        if p.get("txt_type") == 2 and p.get("signature"):  # room server relaying someone else's post
            author = self.contact(p["signature"])
            nick = author["adv_name"] if author else p["signature"]
        rec = {"k": "msg", "nick": nick, "text": p["text"], "hops": p.get("path_len"),
               "snr": p.get("SNR"), "st_ts": p.get("sender_timestamp")}
        self.add(win, rec, activity=3)
        if win is not self.win:
            self.bell()
        if win is self.win and win.kind == "query":
            self.refresh_nicklist()

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
        me = self.my_name.lower()
        hl = f"@[{me}]" in text.lower() or me in text.lower()
        win.speakers[nick] = time.time()
        self.add(win, {"k": "msg", "nick": nick, "text": text, "hl": hl, "hops": p.get("path_len"),
                       "snr": p.get("SNR"), "st_ts": p.get("sender_timestamp")}, activity=3 if hl else 2)
        if hl and win is not self.win:
            self.bell()
        if win is self.win:
            self.refresh_nicklist()
            self.refresh_topic()

    async def on_ack(self, ev) -> None:
        entry = self.pending_acks.pop(ev.payload.get("code", ""), None)
        if entry:
            self.set_delivery(*entry, "ok")

    def set_delivery(self, win: Window, rec: dict, st: str) -> None:
        if rec.get("st") != "pending":
            return
        rec["st"] = st
        if self.store:
            self.store.append(win.key, {"k": "ack", "ref": rec["id"], "st": st})
        if win is self.win:
            self.redraw()

    async def on_advert(self, ev) -> None:
        c = self.contact(ev.payload["public_key"])
        name = c["adv_name"] if c else ev.payload["public_key"][:12]
        self.status(f"Advert from {name}", "dim")
        self.refresh_nicklist()

    async def on_new_contact(self, ev) -> None:
        c = ev.payload
        self.status(f"New node heard: {c['adv_name']} ({CONTACT_TYPES.get(c['type'], '?')}, "
                    f"{c['public_key'][:12]}) — /accept {c['adv_name']}", "join")

    async def on_path_update(self, ev) -> None:
        await asyncio.sleep(0.5)  # let the library refresh its contact table first
        c = self.contact(ev.payload["public_key"])
        if c:
            self.status(f"Route to {c['adv_name']} is now: {self.path_str(c)}", "dim")
            if self.win.kind == "query":
                self.refresh_topic()
                self.refresh_nicklist()

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
                        "connected to it. Disconnect that, then /reconnect.", "error")
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
        if self.mc:
            try:
                await asyncio.wait_for(self.mc.disconnect(), 2)
            except Exception:  # noqa: BLE001
                pass
        self.exit()

    # ── input ─────────────────────────────────────────────────────────────
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
        if win.kind == "status":
            self.echo("Not a chat window. Use /join, /query or switch windows (alt+N, ctrl+n/p).", "error")
            return
        if not self.connected:
            self.echo("Not connected.", "error")
            return
        if win.kind == "channel":
            limit = MAX_TEXT - len(self.my_name.encode()) - 2
            for chunk in split_utf8(text, limit):
                async with self.io:
                    ev = await self.mc.commands.send_chan_msg(win.channel_idx, chunk)
                if ev.is_error():
                    self.echo(f"Send failed: {ev.payload}", "error")
                    return
                self.add(win, {"k": "msg", "nick": self.my_name, "text": chunk, "own": True})
            return
        c = self.contact(win.pubkey)
        if not c:
            self.echo("That contact isn't in the radio's contact list — can't encrypt to it.", "error")
            return
        for chunk in split_utf8(text, MAX_TEXT):
            rec = self.add(win, {"k": "msg", "nick": self.my_name, "text": chunk, "own": True, "st": "pending"})
            async with self.io:
                ev = await self.mc.commands.send_msg(c, chunk)
            if ev.is_error():
                self.set_delivery(win, rec, "fail")
                self.echo(f"Send failed: {ev.payload}", "error")
                return
            code = ev.payload["expected_ack"].hex()
            self.pending_acks[code] = (win, rec)
            timeout = max(ev.payload.get("suggested_timeout", 10000) / 1000 * 1.5, 10)
            self.set_timer(timeout, lambda code=code: self._ack_timeout(code))

    def _ack_timeout(self, code: str) -> None:
        entry = self.pending_acks.pop(code, None)
        if entry:
            self.set_delivery(*entry, "fail")
