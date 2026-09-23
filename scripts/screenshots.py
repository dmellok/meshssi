"""Render README screenshots from made-up data (no radio needed).

    .venv/bin/python scripts/screenshots.py      # writes docs/*.svg, then PNGs via headless Chrome
"""

import asyncio
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from meshcore.events import Event, EventType  # noqa: E402

from meshssi.app import MeshssiApp, Window  # noqa: E402

DOCS = ROOT / "docs"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
NOW = time.time()


def key(seed: str) -> str:
    import hashlib

    return hashlib.sha256(seed.encode()).hexdigest()


def contact(name, type_, hops, advert_ago, lat=0.0, lon=0.0):
    k = key(name)
    return {
        "public_key": k, "type": type_, "flags": 0, "out_path_hash_mode": 0,
        "out_path_len": hops, "out_path": "".join(key(f"hop{i}{name}")[:2] for i in range(max(hops, 0))),
        "adv_name": name, "last_advert": NOW - advert_ago, "adv_lat": lat, "adv_lon": lon, "lastmod": NOW,
    }


CONTACTS = [
    contact("Ridgeline Rpt", 2, 0, 90, -33.861, 151.002),
    contact("Harbour Hill Rpt", 2, 1, 600, -33.842, 151.212),
    contact("ada 🦊", 1, 2, 240),
    contact("bramble", 1, 1, 1500),
    contact("Nora 🌿", 1, -1, 5400),
    contact("Makerspace Room", 3, 1, 3000),
    contact("weather-stn-04", 4, 2, 800),
]
BY_NAME = {c["adv_name"]: c for c in CONTACTS}

SELF_INFO = {
    "adv_type": 1, "tx_power": 20, "max_tx_power": 22, "public_key": key("kestrel"),
    "adv_lat": -33.87, "adv_lon": 151.21, "multi_acks": 0, "adv_loc_policy": 0,
    "telemetry_mode_env": 0, "telemetry_mode_loc": 0, "telemetry_mode_base": 0,
    "manual_add_contacts": True, "radio_freq": 915.8, "radio_bw": 250.0, "radio_sf": 11, "radio_cr": 5,
    "name": "kestrel",
}
DEVICE_INFO = {
    "fw ver": 13, "max_contacts": 350, "max_channels": 40, "ble_pin": 0, "fw_build": "14 Aug 2026",
    "model": "Heltec V3", "ver": "v1.17.1", "repeat": False, "path_hash_mode": 1,
}


class FakeCommands:
    async def send_appstart(self):
        return Event(EventType.SELF_INFO, SELF_INFO)

    async def get_stats_core(self):
        return Event(EventType.STATS_CORE, {"battery_mv": 4012, "uptime_secs": 312_345, "errors": 0, "queue_len": 0})

    async def get_stats_radio(self):
        return Event(EventType.STATS_RADIO, {"noise_floor": -112, "last_rssi": -97, "last_snr": 6.25,
                                             "tx_air_secs": 214, "rx_air_secs": 9120})

    async def get_stats_packets(self):
        return Event(EventType.STATS_PACKETS, {"recv": 18_422, "sent": 311, "flood_tx": 190, "direct_tx": 121,
                                               "flood_rx": 17_020, "direct_rx": 1402, "recv_errors": 37})

    async def get_bat(self):
        return Event(EventType.BATTERY, {"level": 4012, "used_kb": 38, "total_kb": 1404})

    async def req_status_sync(self, c, *a, **kw):
        return {"bat": 3987, "tx_queue_len": 0, "noise_floor": -109, "last_rssi": -88, "nb_recv": 40_211,
                "nb_sent": 22_904, "airtime": 3312, "uptime": 1_904_331, "sent_flood": 20_110,
                "sent_direct": 2794, "recv_flood": 37_002, "recv_direct": 3209, "full_evts": 0,
                "last_snr": 7.5, "direct_dups": 312, "flood_dups": 9120}


class FakeMC:
    def __init__(self):
        self.contacts = {c["public_key"]: c for c in CONTACTS}
        self.pending_contacts = {}
        self.commands = FakeCommands()

    def get_contact_by_key_prefix(self, prefix):
        return next((c for c in CONTACTS if c["public_key"].startswith(prefix)), None)


def msg(nick, text, ago, **kw):
    return {"k": "msg", "nick": nick, "text": text, "t": NOW - ago, **kw}


def note(text, ago, lvl="info"):
    return {"k": "notice", "text": text, "t": NOW - ago, "lvl": lvl}


class DemoApp(MeshssiApp):
    async def connect(self):
        self.mc = FakeMC()
        self.connected = True
        self.self_info = dict(SELF_INFO)
        self.device_info = dict(DEVICE_INFO)
        self.stats = {"noise_floor": -112, "battery_mv": 4012}
        self.channels = {0: {"channel_hash": "11"}, 1: {"channel_hash": "5c"}, 2: {"channel_hash": "a7"}}
        status = self.windows[0]
        status.recs = [
            note("meshssi — an irssi-style MeshCore client. /help for commands.", 3700),
            note("Connecting to 192.168.1.50…", 3700),
            {**note("Connected: kestrel (" + SELF_INFO["public_key"][:12] + ") · Heltec V3 v1.17.1", 3699), "lvl": "ok"},
            {**note("Advert from Ridgeline Rpt", 1900), "lvl": "dim"},
            {**note("New node heard: moth-lite (chat, 3be1a09c77d2) — /accept moth-lite", 1200), "lvl": "join"},
            {**note("Route to ada 🦊 is now: 2 hops via " + BY_NAME["ada 🦊"]["out_path"][:2] + "," + BY_NAME["ada 🦊"]["out_path"][2:4], 700), "lvl": "dim"},
        ]
        pub = self.open_window(Window("channel", "chan:Public", "Public", channel_idx=0))
        pub.recs = [
            msg("bramble", "anyone hearing the new Harbour Hill repeater?", 2900, hops=1, snr=4.5),
            msg("ada 🦊", "yep, 1 hop from here, SNR is great", 2840, hops=2, snr=-3.25),
            msg("Nora 🌿", "just set one up on the balcony, 20dBm into a 5dBi whip", 2400, hops=3, snr=-9.0),
            msg("kestrel", "@[Nora 🌿] nice! got you at -9 SNR via 3 hops", 2380, own=True),
            msg("Nora 🌿", "ha, I'll take it 😅", 2300, hops=3, snr=-8.75),
            msg("bramble", "@[kestrel] are you coming to the meetup saturday?", 1500, hops=1, snr=5.0, hl=True),
            msg("kestrel", "wouldn't miss it, bringing spare RAK boards", 1450, own=True),
            msg("weather-stn-04", "temp 18.4C hum 62% wind 11km/h SW", 900, hops=2, snr=1.5),
            msg("ada 🦊", "flood test from the ridge trail — who copies?", 300, hops=4, snr=-12.5),
            msg("bramble", "copy, 4 hops, -12.5", 280, hops=1, snr=4.75),
        ]
        for r in pub.recs:
            if not r.get("own"):
                pub.speakers[r["nick"]] = r["t"]
        dev = self.open_window(Window("channel", "chan:#mesh-dev", "#mesh-dev", channel_idx=1))
        dev.recs = [msg("ada 🦊", "v1.17.1 fixed the ack timeouts for me", 800, hops=2, snr=-2.0)]
        dev.speakers["ada 🦊"] = NOW - 800
        dev.activity = 2
        hike = self.open_window(Window("channel", "chan:#hiking", "#hiking", channel_idx=2))
        hike.activity = 1
        ada = BY_NAME["ada 🦊"]
        q = self.query_window(ada)
        q.recs = [
            msg("ada 🦊", "hey, is your node repeating yet?", 3300, hops=2, snr=-4.0),
            msg("kestrel", "nope, companion only — Ridgeline covers me", 3250, own=True, st="ok"),
            msg("ada 🦊", "cool. can you send me the #mesh-dev key?", 3200, hops=2, snr=-4.25),
            msg("kestrel", "it's a hashtag channel, just /join #mesh-dev", 3150, own=True, st="ok"),
            msg("kestrel", "sent from the car, might not make it", 2000, own=True, st="fail"),
            msg("kestrel", "trying again from the hill", 1990, own=True, st="ok"),
            msg("ada 🦊", "got that one 👍", 1900, hops=2, snr=-3.5),
            msg("kestrel", "see you saturday", 30, own=True, st="pending"),
        ]
        q.activity = 3
        rpt = self.query_window(BY_NAME["Ridgeline Rpt"])
        rpt.recs = [
            note("Logged into Ridgeline Rpt (admin)", 400, "ok"),
            note("> get radio", 390, "dim"),
            {"k": "reply", "nick": "Ridgeline Rpt", "text": "> 915.800,250.0,11,5", "t": NOW - 385},
            note("> get tx", 380, "dim"),
            {"k": "reply", "nick": "Ridgeline Rpt", "text": "> 22", "t": NOW - 376},
            note("> neighbors", 370, "dim"),
            {"k": "reply", "nick": "Ridgeline Rpt", "text": "> 7ab2c1:118:26 d03e44:1200:-18 5f1a90:3400:9", "t": NOW - 364},
        ]
        self.switch(1)


async def main():
    DOCS.mkdir(exist_ok=True)
    app = DemoApp("192.168.1.50")
    async with app.run_test(size=(132, 34)) as pilot:
        await pilot.pause(0.5)

        async def shot(name):
            await pilot.pause(0.3)
            app.save_screenshot(f"{name}.svg", str(DOCS))

        async def run(cmd):
            await app.run_command(cmd)

        await shot("channel")
        app.switch(app.windows.index(app.find_window("dm:" + BY_NAME["ada 🦊"]["public_key"])))
        await shot("dm")
        rpt = app.find_window("dm:" + BY_NAME["Ridgeline Rpt"]["public_key"])
        app.switch(app.windows.index(rpt))
        await run("rstatus")
        await shot("repeater")
        app.switch(0)
        await run("info")
        await run("stats")
        await shot("device")
    return ["channel", "dm", "repeater", "device"]


def to_png(name: str) -> None:
    svg = DOCS / f"{name}.svg"
    w, h = map(float, re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"', svg.read_text()).groups())
    # an HTML wrapper with zero margin so the capture is exactly the SVG
    html = DOCS / f"_{name}.html"
    html.write_text(f'<html><body style="margin:0;background:#000"><img src="{svg.name}" width="{w}" height="{h}"></body></html>')
    subprocess.run([CHROME, "--headless", "--hide-scrollbars", "--force-device-scale-factor=2",
                    f"--window-size={int(w)},{int(h)}", f"--screenshot={DOCS / (name + '.png')}", html.as_uri()],
                   check=True, capture_output=True)
    html.unlink()
    svg.unlink()


if __name__ == "__main__":
    for n in asyncio.run(main()):
        to_png(n)
        print("wrote", DOCS / f"{n}.png")
