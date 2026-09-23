"""Slash commands. Each handler takes the raw argument string."""

import asyncio
import time
from typing import Callable

from meshcore import EventType
from rich.markup import escape

COMMANDS: dict[str, tuple[Callable, str, str, str]] = {}  # name -> (fn, category, usage, help)
ALIASES: dict[str, str] = {}


def command(name: str, category: str, usage: str, help: str, aliases: tuple[str, ...] = ()):
    def deco(fn):
        COMMANDS[name] = (fn, category, usage, help)
        for a in aliases:
            ALIASES[a] = name
        return fn

    return deco


def fmt_duration(secs: int) -> str:
    d, rem = divmod(int(secs), 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    return (f"{d}d " if d else "") + f"{h:02d}:{m:02d}:{s:02d}"


class CommandsMixin:
    def command_names(self) -> list[str]:
        return list(COMMANDS) + list(ALIASES)

    async def run_command(self, line: str) -> None:
        name, _, args = line.partition(" ")
        name = ALIASES.get(name.lower(), name.lower())
        if name not in COMMANDS:
            self.echo(f"Unknown command: /{name} — try /help", "error")
            return
        fn = COMMANDS[name][0]
        if COMMANDS[name][1] != "client" and not self.connected:
            self.echo("Not connected to a radio.", "error")
            return
        try:
            await fn(self, args.strip())
        except Exception as e:  # noqa: BLE001
            self.echo(f"/{name} failed: {type(e).__name__}: {e}", "error")

    def need_contact(self, query: str) -> dict | None:
        if not query:
            if self.win.kind == "query" and (c := self.contact(self.win.pubkey)):
                return c
            self.echo("Which contact? (tab completes names)", "error")
            return None
        c = self.find_contact(query)
        if not c:
            self.echo(f"No unique contact matches {query!r}. See /contacts.", "error")
        return c

    def confirm(self, action: str) -> bool:
        """Destructive commands must be issued twice within 10 seconds."""
        if self.pending_confirm and self.pending_confirm[0] == action and time.time() - self.pending_confirm[1] < 10:
            self.pending_confirm = None
            return True
        self.pending_confirm = (action, time.time())
        self.echo(f"Repeat the command within 10s to confirm: {action}", "error")
        return False

    async def cmd(self, coro):
        async with self.io:
            ev = await coro
        if ev is not None and ev.type == EventType.ERROR:
            raise RuntimeError(ev.payload.get("reason") or ev.payload)
        return ev

    # ── client ────────────────────────────────────────────────────────────
    @command("help", "client", "/help [command]", "List commands, or show help for one")
    async def c_help(self, args):
        if args:
            name = ALIASES.get(args.lstrip("/"), args.lstrip("/"))
            if name in COMMANDS:
                _, _, usage, text = COMMANDS[name]
                als = [a for a, n in ALIASES.items() if n == name]
                self.echo(f"[bold]{escape(usage)}[/]  {escape(text)}" + (f"  (aliases: {', '.join('/' + a for a in als)})" if als else ""), markup=True)
            else:
                self.echo(f"No such command: {args}", "error")
            return
        cats: dict[str, list[str]] = {}
        for n, (_, cat, usage, text) in COMMANDS.items():
            cats.setdefault(cat, []).append(f"  [bold]{escape(f'{usage:<36}')}[/] [grey62]{escape(text)}[/]")
        for cat in ("chat", "contacts", "remote", "device", "client"):
            self.echo(f"[bold underline]{cat}[/]", markup=True)
            for row in cats.get(cat, []):
                self.echo(row, markup=True)
        self.echo("Keys: alt+1..0 / esc N jump · ctrl+n/p cycle · ctrl+a next active · F2 nicklist · PgUp/PgDn scroll · tab complete")

    @command("window", "client", "/window <n>|close|list", "Switch, close or list windows", aliases=("win", "w"))
    async def c_window(self, args):
        if args.isdigit():
            self.switch(int(args) - 1)
        elif args in ("close", "c"):
            await self.c_wc("")
        else:
            for i, w in enumerate(self.windows):
                self.echo(f"{i + 1:>2}: {w.name:<24} {w.kind}")

    @command("wc", "client", "/wc", "Close the current window (queries; use /part for channels)", aliases=("close",))
    async def c_wc(self, args):
        if self.win.kind == "channel":
            self.echo("Use /part to leave (and delete) a channel.", "error")
        else:
            self.close_window(self.win)

    @command("clear", "client", "/clear", "Clear the current window's scrollback on screen")
    async def c_clear(self, args):
        self.win.recs.clear()
        self.redraw()

    @command("reconnect", "client", "/reconnect [host[:port]]", "Reconnect, optionally to another radio", aliases=("connect", "server"))
    async def c_reconnect(self, args):
        if self.mc:
            try:
                await asyncio.wait_for(self.mc.disconnect(), 3)
            except Exception:  # noqa: BLE001
                pass
        self.connected = False
        self.mc = None
        self.drops.clear()
        if args:
            self.target = args
        self.run_worker(self.connect(), exclusive=True, group="connect")

    @command("quit", "client", "/quit", "Exit meshssi", aliases=("exit", "q!"))
    async def c_quit(self, args):
        await self.action_quit()

    # ── chat ──────────────────────────────────────────────────────────────
    @command("msg", "chat", "/msg <contact|#channel> <text>", "Send a message without switching windows", aliases=("m",))
    async def c_msg(self, args):
        if args.startswith("#") or args.lower().startswith("public "):
            name, _, text = args.partition(" ")
            win = next((w for w in self.windows if w.kind == "channel" and w.name.lower() == name.lower()), None)
            if not win:
                self.echo(f"Not in {name}.", "error")
                return
        else:
            c, text = self.split_target(args)
            if not c:
                self.echo("Usage: /msg <contact> <text> (tab completes names)", "error")
                return
            win = self.query_window(c)
        if text:
            await self.say(win, text)

    @command("query", "chat", "/query <contact>", "Open a DM window with a contact", aliases=("dm",))
    async def c_query(self, args):
        c = self.need_contact(args)
        if c:
            win = self.query_window(c)
            self.switch(self.windows.index(win))

    @command("join", "chat", "/join <#hashtag> | <name> <32-hex-key>", "Add a channel to a free slot on the radio", aliases=("j",))
    async def c_join(self, args):
        parts = args.split()
        if not parts:
            self.echo("Usage: /join #hashtag   or   /join <name> <key-hex>", "error")
            return
        name = parts[0]
        existing = next((w for w in self.windows if w.kind == "channel" and w.name.lower() == name.lower()), None)
        if existing:
            self.switch(self.windows.index(existing))
            return
        if name.startswith("#"):
            secret = None
        elif len(parts) == 2 and len(parts[1]) == 32:
            secret = bytes.fromhex(parts[1])
        else:
            self.echo("Private channels need their 16-byte key as 32 hex chars. Hashtag channels (#name) derive it.", "error")
            return
        free = next((i for i in range(self.device_info.get("max_channels", 8)) if i not in self.channels and i != 0), None)
        if free is None:
            self.echo("No free channel slots on the radio.", "error")
            return
        await self.cmd(self.mc.commands.set_channel(free, name, secret))
        await self.load_channels()
        win = next(w for w in self.windows if w.kind == "channel" and w.channel_idx == free)
        self.switch(self.windows.index(win))
        self.status(f"Joined {name} (slot {free})", "join", win=win)

    @command("part", "chat", "/part [#channel]", "Remove a channel from the radio", aliases=("leave",))
    async def c_part(self, args):
        win = self.win if not args else next((w for w in self.windows if w.kind == "channel" and w.name.lower() == args.lower()), None)
        if not win or win.kind != "channel":
            self.echo("Not a channel.", "error")
            return
        if win.channel_idx == 0 and not self.confirm(f"part {win.name} (slot 0, the public channel)"):
            return
        await self.cmd(self.mc.commands.set_channel(win.channel_idx, "", bytes(16)))
        self.channels.pop(win.channel_idx, None)
        self.close_window(win)
        self.status(f"Left {win.name}", "info")

    @command("channels", "chat", "/channels", "List channels configured on the radio", aliases=("list",))
    async def c_channels(self, args):
        for idx, ch in sorted(self.channels.items()):
            self.echo(f"slot {idx:>2}  {ch['channel_name']:<24} hash {ch['channel_hash']}")

    @command("key", "chat", "/key [#channel]", "Show a channel's secret key (to share it)")
    async def c_key(self, args):
        win = self.win if not args else next((w for w in self.windows if w.kind == "channel" and w.name.lower() == args.lower()), None)
        if not win or win.kind != "channel":
            self.echo("Not a channel.", "error")
            return
        secret = self.channels[win.channel_idx]["channel_secret"]
        self.echo(f"{win.name} key: {secret.hex()}   (others join with /join {win.name} {secret.hex()})")

    # ── contacts ──────────────────────────────────────────────────────────
    @command("contacts", "contacts", "/contacts [filter]", "List contacts stored on the radio", aliases=("who", "names"))
    async def c_contacts(self, args):
        from .app import CONTACT_TYPES

        await self.refresh_contacts()
        cs = sorted(self.mc.contacts.values(), key=lambda c: -c.get("last_advert", 0))
        if args:
            cs = [c for c in cs if args.lower() in c["adv_name"].lower()]
        from .app import ago

        self.echo(f"{len(cs)} contact(s) (of {len(self.mc.contacts)}; radio holds up to {self.device_info.get('max_contacts', '?')})")
        for c in cs:
            self.echo(f"{CONTACT_TYPES.get(c['type'], '?'):<8} {c['adv_name']:<26} {c['public_key'][:12]}  "
                      f"{self.path_str(c):<22} {ago(c.get('last_advert'))}")
        self.refresh_nicklist()

    @command("whois", "contacts", "/whois <contact>", "Show everything known about a contact", aliases=("wi",))
    async def c_whois(self, args):
        from .app import CONTACT_TYPES, ago

        c = self.need_contact(args)
        if not c:
            return
        self.echo(f"[bold]{escape(c['adv_name'])}[/]", markup=True)
        self.echo(f"  type     : {CONTACT_TYPES.get(c['type'], '?')}")
        self.echo(f"  key      : {c['public_key']}")
        self.echo(f"  route    : {self.path_str(c)}")
        self.echo(f"  advert   : {ago(c.get('last_advert'))}")
        if c.get("adv_lat") or c.get("adv_lon"):
            self.echo(f"  location : {c['adv_lat']:.5f}, {c['adv_lon']:.5f}")
        self.echo(f"  flags    : {c.get('flags', 0):#04x}")

    @command("pending", "contacts", "/pending", "List nodes heard but not yet added (manual-add mode)")
    async def c_pending(self, args):
        from .app import CONTACT_TYPES

        pend = self.mc.pending_contacts
        if not pend:
            self.echo("No pending contacts heard this session.")
        for c in pend.values():
            self.echo(f"{CONTACT_TYPES.get(c['type'], '?'):<8} {c['adv_name']:<26} {c['public_key'][:12]}")

    @command("accept", "contacts", "/accept <name|key-prefix|all>", "Add a pending node to the radio's contacts", aliases=("add",))
    async def c_accept(self, args):
        pend = list(self.mc.pending_contacts.values())
        q = args.lower()
        picks = pend if q == "all" else [c for c in pend if c["adv_name"].lower().startswith(q) or c["public_key"].startswith(q)]
        if not picks:
            self.echo("No matching pending contact. See /pending.", "error")
            return
        for c in picks:
            await self.cmd(self.mc.commands.add_contact(c))
            self.mc.pop_pending_contact(c["public_key"])
            self.status(f"Added {c['adv_name']}", "join")
        await self.refresh_contacts()
        self.refresh_nicklist()

    @command("rmcontact", "contacts", "/rmcontact <contact>", "Delete a contact from the radio")
    async def c_rmcontact(self, args):
        c = self.need_contact(args)
        if c and self.confirm(f"rmcontact {c['adv_name']}"):
            await self.cmd(self.mc.commands.remove_contact(c))
            self.mc.contacts.pop(c["public_key"], None)
            self.status(f"Removed {c['adv_name']}")
            self.refresh_nicklist()

    @command("resetpath", "contacts", "/resetpath <contact>", "Forget the stored route; next message floods")
    async def c_resetpath(self, args):
        c = self.need_contact(args)
        if c:
            await self.cmd(self.mc.commands.reset_path(c))
            await self.refresh_contacts()
            self.echo(f"Route to {c['adv_name']} reset to flood.", "ok")

    @command("discover", "contacts", "/discover <contact>", "Find a route to a contact (path discovery)", aliases=("ping",))
    async def c_discover(self, args):
        c = self.need_contact(args)
        if not c:
            return
        self.echo(f"Discovering path to {c['adv_name']}…")
        ev = await self.mc.commands.send_path_discovery_sync(c)
        if not ev:
            self.echo("No response.", "error")
            return
        p = ev.payload
        self.echo(f"Path to {c['adv_name']}: out {p.get('out_path') or 'direct'} "
                  f"({p.get('out_path_len', '?')} hops), back {p.get('in_path') or 'direct'} ({p.get('in_path_len', '?')} hops)", "ok")

    @command("share", "contacts", "/share <contact>", "Re-broadcast a contact's advert zero-hop so neighbours learn it")
    async def c_share(self, args):
        c = self.need_contact(args)
        if c:
            await self.cmd(self.mc.commands.share_contact(c))
            self.echo(f"Shared {c['adv_name']}.", "ok")

    # ── remote nodes (repeaters, rooms, sensors) ──────────────────────────
    @command("login", "remote", "/login <node> [password]", "Log into a repeater/room (blank = guest)")
    async def c_login(self, args):
        c, pwd = self.split_target(args)
        c = c or self.need_contact("")
        if not c:
            return
        self.echo(f"Logging into {c['adv_name']}…")
        ev = await self.mc.commands.send_login_sync(c, pwd)
        if ev and ev.type == EventType.LOGIN_SUCCESS:
            admin = ev.payload.get("is_admin") or ev.payload.get("permissions")
            self.echo(f"Logged into {c['adv_name']}" + (" (admin)" if admin else ""), "ok")
        else:
            self.echo("Login failed or timed out.", "error")

    @command("logout", "remote", "/logout <node>", "Log out of a repeater/room")
    async def c_logout(self, args):
        c = self.need_contact(args)
        if c:
            await self.cmd(self.mc.commands.send_logout(c))
            self.echo(f"Logged out of {c['adv_name']}.", "ok")

    @command("rcmd", "remote", "/rcmd <node> <cli command>", "Run a CLI command on a repeater (login first); reply shows in its window", aliases=("rc",))
    async def c_rcmd(self, args):
        c, cmd = self.split_target(args)
        if not c and self.win.kind == "query":
            c, cmd = self.contact(self.win.pubkey), args
        if not c or not cmd:
            self.echo("Usage: /rcmd <node> <command>, e.g. /rcmd Ridgeline Rpt get radio", "error")
            return
        win = self.query_window(c)
        self.add(win, {"k": "notice", "text": f"> {cmd}", "lvl": "dim"})
        await self.cmd(self.mc.commands.send_cmd(c, cmd))

    @command("rstatus", "remote", "/rstatus <node>", "Request status (uptime, battery, airtime, counters) from a node", aliases=("status",))
    async def c_rstatus(self, args):
        c = self.need_contact(args)
        if not c:
            return
        self.echo(f"Requesting status from {c['adv_name']}…")
        st = await self.mc.commands.req_status_sync(c)
        if not st:
            self.echo("No response (try /login first).", "error")
            return
        self.echo(f"[bold]{escape(c['adv_name'])}[/] status:", markup=True)
        for k, v in st.items():
            if k in ("pubkey_pre", "tag"):
                continue
            if k == "uptime":
                v = fmt_duration(v)
            elif k == "bat":
                v = f"{v / 1000:.2f} V"
            self.echo(f"  {k:<18}: {v}")

    @command("telemetry", "remote", "/telemetry [node]", "Request sensor telemetry (no node = this radio)", aliases=("tele",))
    async def c_telemetry(self, args):
        if not args and self.win.kind != "query":
            ev = await self.cmd(self.mc.commands.get_self_telemetry())
            data, who = ev.payload.get("lpp", ev.payload), self.my_name
        else:
            c = self.need_contact(args)
            if not c:
                return
            self.echo(f"Requesting telemetry from {c['adv_name']}…")
            data, who = await self.mc.commands.req_telemetry_sync(c), c["adv_name"]
            if data is None:
                self.echo("No response.", "error")
                return
        self.echo(f"Telemetry from {who}:")
        for item in data if isinstance(data, list) else [data]:
            if isinstance(item, dict):
                self.echo(f"  ch{item.get('channel', '?')} {item.get('type', '?')}: {item.get('value')}")
            else:
                self.echo(f"  {item}")

    @command("neighbours", "remote", "/neighbours <repeater>", "List a repeater's neighbours with SNR", aliases=("neighbors", "nb"))
    async def c_neighbours(self, args):
        c = self.need_contact(args)
        if not c:
            return
        self.echo(f"Requesting neighbours from {c['adv_name']}…")
        res = await self.mc.commands.fetch_all_neighbours(c)
        if not res:
            self.echo("No response (try /login first).", "error")
            return
        items = res.get("neighbours", res) if isinstance(res, dict) else res
        from .app import ago

        for n in items:
            key = n.get("pubkey", "")
            known = self.contact(key) if len(key) >= 8 else None
            heard = time.time() - n.get("secs_ago", 0) if "secs_ago" in n else None
            self.echo(f"  {(known['adv_name'] if known else key):<26} snr {n.get('snr', '?'):>6}  {ago(heard) if heard else ''}")

    # ── device maintenance ────────────────────────────────────────────────
    @command("info", "device", "/info", "Radio identity, firmware and settings", aliases=("sysinfo",))
    async def c_info(self, args):
        ev = await self.cmd(self.mc.commands.send_appstart())
        si = self.self_info = dict(ev.payload)
        d = self.device_info
        self.echo(f"[bold]{escape(si['name'])}[/]  {si['public_key']}", markup=True)
        self.echo(f"  model     : {d.get('model')}  fw {d.get('ver')} (build {d.get('fw_build')}, proto {d.get('fw ver')})")
        self.echo(f"  radio     : {si['radio_freq']:.3f} MHz  BW {si['radio_bw']:g} kHz  SF{si['radio_sf']}  CR{si['radio_cr']}")
        self.echo(f"  tx power  : {si['tx_power']} dBm (max {si['max_tx_power']})")
        self.echo(f"  location  : {si['adv_lat']:.5f}, {si['adv_lon']:.5f}  (advert location policy {si['adv_loc_policy']})")
        self.echo(f"  contacts  : manual add {'on' if si['manual_add_contacts'] else 'off'} · max {d.get('max_contacts')} · channels max {d.get('max_channels')}")
        self.echo(f"  multi-acks: {si['multi_acks']} · telemetry base/loc/env: {si['telemetry_mode_base']}/{si['telemetry_mode_loc']}/{si['telemetry_mode_env']}")
        self.echo(f"  repeat    : {'on' if d.get('repeat') else 'off'} · path hash mode {d.get('path_hash_mode')} · BLE pin {d.get('ble_pin') or 'none'}")
        self.refresh_chrome()

    @command("stats", "device", "/stats", "Uptime, battery, storage, noise floor, airtime and packet counters", aliases=("battery", "bat"))
    async def c_stats(self, args):
        s = {}
        for fn in (self.mc.commands.get_stats_core, self.mc.commands.get_stats_radio, self.mc.commands.get_stats_packets, self.mc.commands.get_bat):
            ev = await self.cmd(fn())
            s.update(ev.payload)
        self.stats.update(s)
        up = s.get("uptime_secs", 0)
        self.echo(f"  uptime    : {fmt_duration(up)}   errors {s.get('errors')}   tx queue {s.get('queue_len')}")
        bat = f"{s['battery_mv'] / 1000:.2f} V" if s.get("battery_mv") else "not reported"
        self.echo(f"  battery   : {bat}   storage {s.get('used_kb')}/{s.get('total_kb')} KB")
        self.echo(f"  radio     : noise floor {s.get('noise_floor')} dBm · last RSSI {s.get('last_rssi')} dBm · last SNR {s.get('last_snr')} dB")
        duty = 100 * s.get("tx_air_secs", 0) / up if up else 0
        self.echo(f"  airtime   : tx {s.get('tx_air_secs')}s ({duty:.2f}% duty) · rx {s.get('rx_air_secs')}s")
        self.echo(f"  packets   : rx {s.get('recv')} (flood {s.get('flood_rx')}, direct {s.get('direct_rx')}, errors {s.get('recv_errors')}) · "
                  f"tx {s.get('sent')} (flood {s.get('flood_tx')}, direct {s.get('direct_tx')})")
        self.refresh_statusbar()

    @command("time", "device", "/time [sync]", "Show the radio clock, or set it from this computer")
    async def c_time(self, args):
        if args == "sync":
            await self.cmd(self.mc.commands.set_time(int(time.time())))
            self.echo("Radio clock set from local time.", "ok")
        ev = await self.cmd(self.mc.commands.get_time())
        t = ev.payload["time"]
        self.echo(f"Radio time: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(t))} (drift {t - time.time():+.0f}s)")

    @command("advert", "device", "/advert [flood]", "Send an advert (zero-hop, or flooded across the mesh)")
    async def c_advert(self, args):
        await self.cmd(self.mc.commands.send_advert(flood=args == "flood"))
        self.echo(f"Sent {'flood' if args == 'flood' else 'zero-hop'} advert.", "ok")

    @command("nick", "device", "/nick <name>", "Change this node's advertised name", aliases=("name",))
    async def c_nick(self, args):
        if not args:
            self.echo(f"You are {self.my_name}.")
            return
        await self.cmd(self.mc.commands.set_name(args))
        await self.cmd(self.mc.commands.send_appstart())
        self.status(f"You are now known as {args} (send /advert so others see it)", "ok")
        self.refresh_chrome()

    @command("txpower", "device", "/txpower <dBm>", "Set transmit power")
    async def c_txpower(self, args):
        if not args.lstrip("-").isdigit():
            self.echo(f"TX power is {self.self_info['tx_power']} dBm (max {self.self_info['max_tx_power']}).")
            return
        await self.cmd(self.mc.commands.set_tx_power(int(args)))
        await self.cmd(self.mc.commands.send_appstart())
        self.echo(f"TX power set to {args} dBm.", "ok")

    @command("radio", "device", "/radio <MHz> <bw kHz> <sf> <cr>", "Set LoRa parameters (must match your mesh!)")
    async def c_radio(self, args):
        p = args.split()
        if len(p) != 4:
            si = self.self_info
            self.echo(f"Radio: {si['radio_freq']:.3f} MHz, BW {si['radio_bw']:g} kHz, SF{si['radio_sf']}, CR{si['radio_cr']}. "
                      "Usage: /radio 916.575 62.5 7 7")
            return
        freq, bw, sf, cr = float(p[0]), float(p[1]), int(p[2]), int(p[3])
        if not self.confirm(f"radio {freq} {bw} {sf} {cr} — a mismatch takes you off the mesh"):
            return
        await self.cmd(self.mc.commands.set_radio(freq, bw, sf, cr))
        self.echo("Radio parameters saved; they take effect after /reboot.", "ok")

    @command("coords", "device", "/coords <lat> <lon>", "Set this node's advertised location")
    async def c_coords(self, args):
        p = args.replace(",", " ").split()
        if len(p) != 2:
            self.echo("Usage: /coords -33.8688 151.2093", "error")
            return
        await self.cmd(self.mc.commands.set_coords(float(p[0]), float(p[1])))
        await self.cmd(self.mc.commands.send_appstart())
        self.echo("Location saved.", "ok")

    @command("set", "device", "/set <manualadd|multiacks|locpolicy> <value>", "Change a companion setting")
    async def c_set(self, args):
        key, _, val = args.partition(" ")
        on = val.lower() in ("on", "1", "yes", "true")
        c = self.mc.commands
        if key == "manualadd":
            await self.cmd(c.set_manual_add_contacts(on))
        elif key == "multiacks":
            await self.cmd(c.set_multi_acks(int(val)))
        elif key == "locpolicy":
            await self.cmd(c.set_advert_loc_policy(int(val)))
        else:
            self.echo("Settings: manualadd on|off · multiacks 0|1|2 · locpolicy 0|1", "error")
            return
        await self.cmd(c.send_appstart())
        self.echo(f"{key} = {val}", "ok")

    @command("cli", "device", "/cli <command>", "Run a raw firmware CLI command on this radio")
    async def c_cli(self, args):
        ev = await self.cmd(self.mc.commands.run_cli_command(args))
        self.echo(str(ev.payload))

    @command("refresh", "device", "/refresh", "Reload identity, contacts and channels from the radio", aliases=("rehash",))
    async def c_refresh(self, args):
        await self.cmd(self.mc.commands.send_appstart())
        await self.refresh_contacts()
        await self.load_channels()
        self.refresh_chrome()
        self.echo("Refreshed.", "ok")

    @command("reboot", "device", "/reboot", "Reboot the radio (asks for confirmation)")
    async def c_reboot(self, args):
        if self.confirm("reboot the radio"):
            try:
                await asyncio.wait_for(self.mc.commands.reboot(), 3)
            except Exception:  # noqa: BLE001 - it usually drops the link before replying
                pass
            self.status("Reboot sent; will reconnect when it's back.", "info")
