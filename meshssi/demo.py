"""A simulated radio and mesh, for `meshssi --demo`, the screenshots, and the tests.

FakeMeshCore mimics the parts of meshcore.MeshCore the app uses (commands, events, contacts), so the
demo exercises the real app code paths: sending, acks, retries, the packet log, traces, heard-by counts.
"""

import asyncio
import hashlib
import random
import tempfile
import time
from pathlib import Path

from meshcore.events import Event, EventType

from .app import MeshssiApp
from .config import Config
from .store import Store

NOW = time.time()


def key(seed: str) -> str:
    return hashlib.sha256(seed.encode()).hexdigest()


def contact(name, type_, hops, advert_ago, lat=0.0, lon=0.0):
    return {
        "public_key": key(name), "type": type_, "flags": 0, "out_path_hash_mode": 0,
        "out_path_len": hops, "out_path": "".join(key("Ridgeline Rpt")[:2] if i == 0 else key(f"hop{i}{name}")[:2]
                                                  for i in range(max(hops, 0))),
        "adv_name": name, "last_advert": NOW - advert_ago, "adv_lat": lat, "adv_lon": lon, "lastmod": NOW,
    }


CONTACTS = [
    contact("Ridgeline Rpt", 2, 0, 90, -33.795, 151.080),
    contact("Harbour Hill Rpt", 2, 1, 600, -33.842, 151.232),
    contact("ada 🦊", 1, 2, 240, -33.760, 151.155),
    contact("bramble", 1, 1, 1500, -33.905, 151.170),
    contact("Nora 🌿", 1, -1, 5400),
    contact("Makerspace Room", 3, 1, 3000, -33.889, 151.199),
    contact("weather-stn-04", 4, 2, 800, -33.720, 151.010),
]
BY_NAME = {c["adv_name"]: c for c in CONTACTS}
STRANGERS = [  # heard over the air, not in contacts
    ("Blue Mtns Rpt", 2, -33.712, 150.311), ("Coogee Rpt", 2, -33.921, 151.259), ("moth-lite", 1, None, None),
    ("Parramatta Rpt", 2, -33.815, 151.003), ("kite", 1, -33.874, 151.100),
]
SELF_INFO = {
    "adv_type": 1, "tx_power": 20, "max_tx_power": 22, "public_key": key("kestrel"),
    "adv_lat": -33.8688, "adv_lon": 151.2093, "multi_acks": 0, "adv_loc_policy": 0,
    "telemetry_mode_env": 0, "telemetry_mode_loc": 0, "telemetry_mode_base": 0,
    "manual_add_contacts": True, "radio_freq": 915.8, "radio_bw": 250.0, "radio_sf": 11, "radio_cr": 5,
    "name": "kestrel",
}
DEVICE_INFO = {
    "fw ver": 13, "max_contacts": 350, "max_channels": 40, "ble_pin": 0, "fw_build": "14 Aug 2026",
    "model": "Heltec V3", "ver": "v1.17.1", "repeat": False, "path_hash_mode": 0,
}
CHANNELS = {0: ("Public", "8b3387e9c5cdea6ac9e5edbaa115cd72"), 1: ("#mesh-dev", None), 2: ("#hiking", None)}
CHATTER = [
    ("Public", "bramble", "anyone hearing the new Harbour Hill repeater?"),
    ("Public", "ada 🦊", "yep, 1 hop from here, SNR is great"),
    ("Public", "Nora 🌿", "just set one up on the balcony, 20dBm into a 5dBi whip"),
    ("#mesh-dev", "ada 🦊", "v1.17.1 fixed the ack timeouts for me"),
    ("Public", "kite", "morning mesh ☕"),
    ("#hiking", "bramble", "Blue Mountains trail had coverage the whole way via Blue Mtns Rpt"),
    ("Public", "weather-stn-04", "temp 18.4C hum 62% wind 11km/h SW"),
    ("#mesh-dev", "kite", "anyone tried the new flood scopes? /scope works nicely"),
    ("Public", "moth-lite", "first message from my new T-Deck 🎉"),
]


class FakeCommands:
    def __init__(self, mc: "FakeMeshCore"):
        self.mc = mc

    def __getattr__(self, name):
        async def ok(*a, **kw):
            return Event(EventType.OK, {})

        return ok

    def _ev(self, t, payload, attrs=None):
        return Event(t, payload, attrs or {})

    async def send_appstart(self):
        return self._ev(EventType.SELF_INFO, dict(self.mc.self_info))

    async def send_device_query(self):
        return self._ev(EventType.DEVICE_INFO, dict(DEVICE_INFO))

    async def get_contacts_async(self, lastmod=0):
        self.mc.later(0.01, Event(EventType.CONTACTS, {c["public_key"]: c for c in self.mc.contacts.values()}))

    async def get_channel(self, idx):
        if idx >= 8:
            return self._ev(EventType.ERROR, {"reason": "not found"})
        name, secret = CHANNELS.get(idx, ("", None))
        sec = bytes.fromhex(secret) if secret else (hashlib.sha256(name.encode()).digest()[:16] if name else bytes(16))
        return self._ev(EventType.CHANNEL_INFO, {"channel_idx": idx, "channel_name": name, "channel_secret": sec,
                                                "channel_hash": hashlib.sha256(sec).hexdigest()[:2]})

    async def get_time(self):
        return self._ev(EventType.CURRENT_TIME, {"time": int(time.time())})

    async def get_stats_core(self):
        return self._ev(EventType.STATS_CORE, {"battery_mv": 4012 - int((time.time() - NOW) / 60), "uptime_secs": 312_345 + int(time.time() - NOW),
                                               "errors": 0, "queue_len": 0})

    async def get_stats_radio(self):
        return self._ev(EventType.STATS_RADIO, {"noise_floor": random.randint(-116, -108), "last_rssi": random.randint(-110, -60),
                                                "last_snr": round(random.uniform(-8, 12) * 4) / 4,
                                                "tx_air_secs": 214 + self.mc.tx, "rx_air_secs": 9120 + int(time.time() - NOW) // 20})

    async def get_stats_packets(self):
        up = int(time.time() - NOW)
        return self._ev(EventType.STATS_PACKETS, {"recv": 18_422 + up // 4 + len(self.mc.rx_log), "sent": 311 + self.mc.tx,
                                                  "flood_tx": 190, "direct_tx": 121, "flood_rx": 17_020, "direct_rx": 1402,
                                                  "recv_errors": 37})

    async def get_bat(self):
        return self._ev(EventType.BATTERY, {"level": 4012, "used_kb": 38, "total_kb": 1404})

    async def get_tuning(self):
        return self._ev(EventType.TUNING_PARAMS, {"rx_delay": 0, "airtime_factor": 1})

    async def get_msg(self, timeout=None):
        if self.mc.inbox:
            ev = self.mc.inbox.pop(0)
            await self.mc.emit(ev)
            return ev
        return self._ev(EventType.NO_MORE_MSGS, {"messages_available": False})

    async def send_chan_msg(self, chan, msg, timestamp=None):
        self.mc.tx += 1
        name = CHANNELS.get(chan, ("?", None))[0]
        for i in range(random.randint(1, 3)):  # repeaters relaying our flood back to us: heard-by counts
            self.mc.later(1 + i * 1.3, self.mc.rx_event("GRP_TXT", f"{self.mc.self_info['name']}: {msg}",
                                                         chan_name=name, sender_timestamp=timestamp))
        return self._ev(EventType.OK, {})

    async def send_msg(self, dst, msg, timestamp=None, attempt=0):
        self.mc.tx += 1
        code = random.randbytes(4)
        if random.random() < 0.8:
            self.mc.later(random.uniform(0.8, 3), Event(EventType.ACK, {"code": code.hex(), "trip_time": random.randint(900, 4000)},
                                                         {"code": code.hex()}))
            name = dst["adv_name"] if isinstance(dst, dict) else "?"
            if random.random() < 0.5 and name in BY_NAME and BY_NAME[name]["type"] == 1:
                self.mc.queue_dm(name, random.choice(["👍", "sounds good", "ok!", "haha yes", "on my way"]), delay=random.uniform(4, 9))
        return self._ev(EventType.MSG_SENT, {"type": 0, "expected_ack": code, "suggested_timeout": 4000},
                        {"expected_ack": code.hex()})

    async def send_cmd(self, dst, cmd, timestamp=None, dst_type=None):
        replies = {"get radio": "> 915.800,250.0,11,5", "get tx": "> 22", "ver": "> v1.17.1 (Build: 14-Aug-2026)",
                   "clock": f"> {time.strftime('%H:%M - %d/%m/%Y')}", "neighbors": "> 7ab2c1:118:26 d03e44:1200:-18"}
        self.mc.queue_dm(dst["adv_name"], replies.get(cmd, f"> OK - {cmd}"), txt_type=1, delay=1.5)
        return self._ev(EventType.MSG_SENT, {"type": 0, "expected_ack": random.randbytes(4), "suggested_timeout": 4000})

    async def send_login_sync(self, dst, pwd, timeout=0, min_timeout=0):
        await asyncio.sleep(1)
        return self._ev(EventType.LOGIN_SUCCESS, {"permissions": 3, "is_admin": True})

    async def req_status_sync(self, c, *a, **kw):
        await asyncio.sleep(1.2)
        up = 1_904_331 + int(time.time() - NOW)
        return {"bat": random.choice([3987, 4102, 3650]), "tx_queue_len": 0, "noise_floor": random.randint(-112, -104),
                "last_rssi": -88, "nb_recv": 40_211 + up // 30, "nb_sent": 22_904 + up // 60, "airtime": 3312 + up // 600,
                "uptime": up, "sent_flood": 20_110, "sent_direct": 2794, "recv_flood": 37_002, "recv_direct": 3209,
                "full_evts": 0, "last_snr": 7.5, "direct_dups": 312, "flood_dups": 9120}

    async def req_telemetry_sync(self, c, *a, **kw):
        await asyncio.sleep(1)
        return [{"channel": 1, "type": "voltage", "value": 4.05}, {"channel": 2, "type": "temperature", "value": 18.4},
                {"channel": 2, "type": "humidity", "value": 62.0}]

    async def fetch_all_neighbours(self, c, *a, **kw):
        await asyncio.sleep(1)
        return {"neighbours": [{"pubkey": BY_NAME["Harbour Hill Rpt"]["public_key"][:8], "secs_ago": 118, "snr": 6.5},
                               {"pubkey": key("Parramatta Rpt")[:8], "secs_ago": 1200, "snr": -4.5},
                               {"pubkey": key("Blue Mtns Rpt")[:8], "secs_ago": 3400, "snr": -11.25}]}

    async def req_acl_sync(self, c, *a, **kw):
        return [{"key": SELF_INFO["public_key"][:12], "perm": 3}, {"key": BY_NAME["ada 🦊"]["public_key"][:12], "perm": 2}]

    async def req_regions_sync(self, c, *a, **kw):
        return "sydney,nsw"

    async def req_owner_sync(self, c, *a, **kw):
        return {"name": c["adv_name"], "owner": "Sydney Mesh Club"}

    async def send_path_discovery_sync(self, c, *a, **kw):
        await asyncio.sleep(1.5)
        return self._ev(EventType.PATH_RESPONSE, {"out_path": c.get("out_path", ""), "out_path_len": max(c.get("out_path_len", 0), 0),
                                                  "out_path_hash_len": 1, "in_path": c.get("out_path", ""),
                                                  "in_path_len": max(c.get("out_path_len", 0), 0), "in_path_hash_len": 1})

    async def send_trace(self, auth_code=0, tag=None, flags=None, path=None):
        tag_b = random.randbytes(4)
        hops = [h for h in (path or "").split(",") if h]
        nodes = [{"hash": h, "snr": round(random.uniform(-10, 12) * 4) / 4} for h in hops] + [{"snr": round(random.uniform(0, 12) * 4) / 4}]
        self.mc.later(2, Event(EventType.TRACE_DATA, {"tag": int.from_bytes(tag_b, "little"), "path": nodes, "path_len": len(hops)},
                               {"tag": int.from_bytes(tag_b, "little")}))
        return self._ev(EventType.MSG_SENT, {"type": 1, "expected_ack": tag_b, "suggested_timeout": 5000})

    async def export_contact(self, c=None):
        k = c["public_key"] if c else SELF_INFO["public_key"]
        return self._ev(EventType.CONTACT_URI, {"uri": "meshcore://" + ("11" + k + "00" * 24)})

    async def get_custom_vars(self):
        return self._ev(EventType.CUSTOM_VARS, {"gps": "0"})

    async def get_default_flood_scope(self):
        return self._ev(EventType.DEFAULT_FLOOD_SCOPE, {"scope_name": ""})

    async def send_node_discover_req(self, bits, prefix_only=True, tag=None, since=None):
        tag = random.randint(1, 2**32 - 1)
        for i, name in enumerate(("Ridgeline Rpt", "Harbour Hill Rpt")):
            k = BY_NAME[name]["public_key"]
            self.mc.later(1 + i, Event(EventType.DISCOVER_RESPONSE, {"node_type": 2, "SNR_in": 8.5 - 9 * i, "SNR": 7.0 - 8 * i,
                                                                     "tag": tag.to_bytes(4, "little").hex(), "pubkey": k}))
        return self._ev(EventType.OK, {"tag": tag})


class FakeMeshCore:
    def __init__(self, live: bool = True):
        self.self_info = dict(SELF_INFO)
        self.contacts = {c["public_key"]: dict(c) for c in CONTACTS}
        self.pending_contacts = {}
        self.commands = FakeCommands(self)
        self.subs: list[tuple] = []
        self.inbox: list[Event] = []
        self.rx_log: list = []
        self.tx = 0
        self.live = live
        self._task = asyncio.get_running_loop().create_task(self.simulate()) if live else None

    # the subset of MeshCore's API the app uses
    def subscribe(self, etype, cb, attribute_filters=None):
        entry = (etype, cb, attribute_filters or {})
        self.subs.append(entry)

        class Sub:
            def unsubscribe(_self):
                if entry in self.subs:
                    self.subs.remove(entry)

        return Sub()

    async def wait_for_event(self, etype, attribute_filters=None, timeout=None):
        fut = asyncio.get_running_loop().create_future()
        sub = self.subscribe(etype, lambda e: fut.done() or fut.set_result(e), attribute_filters)
        try:
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            return None
        finally:
            sub.unsubscribe()

    async def emit(self, ev: Event) -> None:
        for etype, cb, filt in list(self.subs):
            if etype not in (None, ev.type) or any(ev.attributes.get(k) != v for k, v in filt.items()):
                continue
            r = cb(ev)
            if asyncio.iscoroutine(r):
                await r

    def later(self, delay: float, ev: Event) -> None:
        async def go():
            await asyncio.sleep(delay)
            await self.emit(ev)

        asyncio.get_running_loop().create_task(go())

    def get_contact_by_key_prefix(self, prefix):
        return next((c for c in self.contacts.values() if c["public_key"].startswith(prefix)), None)

    def pop_pending_contact(self, k):
        return self.pending_contacts.pop(k, None)

    def set_decrypt_channel_logs(self, v):
        pass

    async def disconnect(self):
        if self._task:
            self._task.cancel()

    # simulation
    def rx_event(self, ptype, message=None, **extra) -> Event:
        hops = [key(f"hop{random.randint(0, 5)}")[:2] for _ in range(random.randint(0, 4))]
        p = {"recv_time": time.time(), "snr": round(random.uniform(-12, 12) * 4) / 4, "rssi": random.randint(-118, -55),
             "route_typename": random.choice(["FLOOD", "TC_FLOOD"]), "payload_typename": ptype,
             "path_hash_size": 1, "path": "".join(hops), "path_len": len(hops), "payload_length": random.randint(20, 120),
             "pkt_hash": random.randint(0, 2**32)}
        if message is not None:
            p["message"] = message
        p.update(extra)
        self.rx_log.append(p)
        return Event(EventType.RX_LOG_DATA, p, {})

    def queue_dm(self, name, text, txt_type=0, delay=1.0, signature=None):
        c = BY_NAME.get(name)
        prefix = c["public_key"][:12] if c else key(name)[:12]
        p = {"type": "PRIV", "pubkey_prefix": prefix, "path_len": max(c["out_path_len"], 0) if c else 1, "txt_type": txt_type,
             "sender_timestamp": int(time.time()), "text": text, "SNR": round(random.uniform(-8, 10) * 4) / 4}
        if signature:
            p["signature"] = signature
        self.inbox.append(Event(EventType.CONTACT_MSG_RECV, p, {"pubkey_prefix": prefix}))
        self.later(delay, Event(EventType.MESSAGES_WAITING, {}))

    def queue_chan(self, chan_name, nick, text, delay=0.5):
        idx = next(i for i, (n, _) in CHANNELS.items() if n == chan_name)
        hops = random.randint(0, 4)
        p = {"type": "CHAN", "channel_idx": idx, "path_len": hops, "txt_type": 0, "sender_timestamp": int(time.time()),
             "text": f"{nick}: {text}", "SNR": round(random.uniform(-10, 11) * 4) / 4}
        self.inbox.append(Event(EventType.CHANNEL_MSG_RECV, p, {"channel_idx": idx}))
        self.later(delay, Event(EventType.MESSAGES_WAITING, {}))
        self.later(delay, self.rx_event("GRP_TXT", f"{nick}: {text}", chan_name=chan_name))

    async def simulate(self) -> None:
        i = 0
        await asyncio.sleep(3)
        while True:
            await asyncio.sleep(random.uniform(3, 7))
            roll = random.random()
            if roll < 0.35:
                chan, nick, text = CHATTER[i % len(CHATTER)]
                i += 1
                self.queue_chan(chan, nick, text)
            elif roll < 0.7:
                name, t, lat, lon = random.choice(STRANGERS + [(c["adv_name"], c["type"], c["adv_lat"], c["adv_lon"]) for c in CONTACTS])
                extra = {"adv_name": name, "adv_type": t, "adv_key": key(name)}
                if lat:
                    extra.update(adv_lat=lat, adv_lon=lon)
                await self.emit(self.rx_event("ADVERT", **extra))
            elif roll < 0.8:
                await self.emit(self.rx_event("GRP_TXT", chan_hash=random.choice(["3f", "c1", "9a"])))
            elif roll < 0.9:
                await self.emit(self.rx_event(random.choice(["ACK", "PATH", "TXT_MSG", "REQ", "RESPONSE"])))
            else:
                self.queue_dm("ada 🦊", random.choice(["you around?", "check #mesh-dev", "nice trace results"]))


def seed_history(app: MeshssiApp) -> None:
    """Scrollback for the screenshots, as if the client had been running for an hour."""

    def msg(nick, text, ago, **kw):
        return {"k": "msg", "nick": nick, "text": text, "t": time.time() - ago, **kw}

    def note(text, ago, lvl="info"):
        return {"k": "notice", "text": text, "t": time.time() - ago, "lvl": lvl}

    pub = app.find_window("chan:Public")
    pub.recs = [
        msg("bramble", "anyone hearing the new Harbour Hill repeater?", 2900, hops=1, snr=4.5),
        msg("ada 🦊", "yep, 1 hop from here, SNR is great", 2840, hops=2, snr=-3.25),
        msg("Nora 🌿", "just set one up on the balcony, 20dBm into a 5dBi whip", 2400, hops=3, snr=-9.0),
        msg("kestrel", "@[Nora 🌿] nice! got you at -9 SNR via 3 hops", 2380, own=True, heard=3),
        msg("Nora 🌿", "ha, I'll take it 😅", 2300, hops=3, snr=-8.75),
        msg("bramble", "@[kestrel] are you coming to the meetup saturday? details https://example.org/meetup", 1500,
            hops=1, snr=5.0, hl=True),
        msg("kestrel", "wouldn't miss it, bringing spare RAK boards", 1450, own=True, heard=2),
        msg("weather-stn-04", "temp 18.4C hum 62% wind 11km/h SW", 900, hops=2, snr=1.5),
        msg("ada 🦊", "flood test from the ridge trail — who copies?", 300, hops=4, snr=-12.5),
        msg("bramble", "copy, 4 hops, -12.5", 280, hops=1, snr=4.75),
    ]
    pub.marker = pub.recs[6]["id"] = "seed-marker"
    for r in pub.recs:
        if not r.get("own"):
            pub.speakers[r["nick"]] = r["t"]
    dev = app.find_window("chan:#mesh-dev")
    dev.recs = [msg("ada 🦊", "v1.17.1 fixed the ack timeouts for me", 800, hops=2, snr=-2.0)]
    dev.speakers["ada 🦊"] = time.time() - 800
    dev.activity = 2
    app.find_window("chan:#hiking").activity = 1
    q = app.query_window(BY_NAME["ada 🦊"])
    q.recs = [
        msg("ada 🦊", "hey, is your node repeating yet?", 3300, hops=2, snr=-4.0),
        msg("kestrel", "nope, companion only — Ridgeline covers me", 3250, own=True, st="ok", rtt=1840),
        msg("ada 🦊", "cool. can you send me the #mesh-dev key?", 3200, hops=2, snr=-4.25),
        msg("kestrel", "it's a hashtag channel, just /join #mesh-dev", 3150, own=True, st="ok", rtt=2210),
        msg("kestrel", "sent from the car, might not make it", 2000, own=True, st="fail"),
        note("No ack from ada 🦊 on its stored route; flooding instead.", 1995, "dim"),
        msg("kestrel", "trying again from the hill", 1990, own=True, st="ok", rtt=3120),
        msg("ada 🦊", "got that one 👍", 1900, hops=2, snr=-3.5),
        msg("kestrel", "see you saturday", 30, own=True, st="pending", **{"try": 2}),
    ]
    q.activity = 3
    rpt = app.query_window(BY_NAME["Ridgeline Rpt"])
    rpt.recs = [
        note("Logged into Ridgeline Rpt (admin)", 400, "ok"),
        note("> get radio", 390, "dim"),
        {"k": "reply", "nick": "Ridgeline Rpt", "text": "> 915.800,250.0,11,5", "t": time.time() - 385},
        note("> ver", 380, "dim"),
        {"k": "reply", "nick": "Ridgeline Rpt", "text": "> v1.17.1 (Build: 14-Aug-2026)", "t": time.time() - 376},
    ]
    for name, t, lat, lon in STRANGERS:
        app.note_heard(key(name), name, t, lat, lon, time.time() - random.randint(60, 3000), snr=round(random.uniform(-12, 10), 1))
    for c in CONTACTS:
        for j in range(40):
            app.snr_hist.setdefault(c["adv_name"], __import__("collections").deque(maxlen=200)).append(
                (time.time() - (40 - j) * 60, round(random.gauss(3 if c["type"] == 2 else -2, 3), 2)))
    base = time.time() - 60 * 60
    for j in range(120):
        app.samples.append({"t": base + j * 30, "noise_floor": -112 + int(3 * random.random()) + (4 if 60 < j < 70 else 0),
                            "last_rssi": random.randint(-105, -70), "last_snr": round(random.uniform(-6, 10), 2),
                            "recv": 18_000 + j * 7 + random.randint(0, 5), "sent": 300 + j // 5,
                            "tx_air_secs": 200 + j // 4, "rx_air_secs": 9000 + j * 3, "battery_mv": 4080 - j})
    # a page of packet history
    fake = app.mc
    for j in range(60):
        name, t, lat, lon = random.choice(STRANGERS + [(c["adv_name"], c["type"], c["adv_lat"], c["adv_lon"]) for c in CONTACTS])
        ev = random.random()
        if ev < 0.4:
            e = fake.rx_event("ADVERT", adv_name=name, adv_type=t, adv_key=key(name), **({"adv_lat": lat, "adv_lon": lon} if lat else {}))
        elif ev < 0.7:
            chan, nick, text = random.choice(CHATTER)
            e = fake.rx_event("GRP_TXT", f"{nick}: {text}", chan_name=chan)
        elif ev < 0.8:
            e = fake.rx_event("GRP_TXT", chan_hash=random.choice(["3f", "c1"]))
        else:
            e = fake.rx_event(random.choice(["ACK", "PATH", "TXT_MSG", "REQ"]))
        e.payload["recv_time"] = time.time() - (60 - j) * 20
        from . import packets

        app.rf.append(packets.summarize(e.payload, app.resolve_hash))
    for key_ in (BY_NAME["Ridgeline Rpt"]["public_key"], BY_NAME["Harbour Hill Rpt"]["public_key"]):
        app.dash[key_] = (time.time() - 120, None)
    for w in app.windows:
        for i, r in enumerate(w.recs):
            r.setdefault("id", f"seed-{w.key}-{i}")


class DemoApp(MeshssiApp):
    """The real app, wired to a simulated radio, with its data kept out of your real config and logs."""

    def __init__(self, live: bool = True, seed: bool = True):
        self._tmp = tempfile.TemporaryDirectory(prefix="meshssi-demo-")
        tmp = Path(self._tmp.name)
        super().__init__("demo", config=Config(tmp / "config.toml"))
        self.live = live
        self.seed = seed
        self.plugins.load_all = lambda: []  # keep the demo independent of your plugins

    async def open_radio(self):
        return FakeMeshCore(live=self.live)

    async def on_connected(self):
        await super().on_connected()
        if self.seed:
            seed_history(self)
            for key_ in list(self.dash):
                self.dash[key_] = (time.time() - 120, await self.mc.commands.req_status_sync(self.contact(key_)))
            self.switch(1)

    def make_store(self, node_key: str) -> Store:
        return Store(node_key, root=Path(self._tmp.name) / "data")
