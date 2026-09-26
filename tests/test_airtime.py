"""meshssi must not flood the mesh on its own: count what goes on the air in worst-case situations."""

import asyncio
import os
import time

from meshcore.events import Event, EventType

from meshssi.demo import BY_NAME, DemoApp

from .test_app import boot


def run(fn, **cfg):
    async def main():
        app = DemoApp(live=False)
        for dotted, value in cfg.items():
            section, key = dotted.split("__")
            app.cfg[section][key] = value
        async with app.run_test(size=(120, 36)) as pilot:
            await boot(pilot, app)
            await fn(app, pilot)
            await app.action_quit()

    asyncio.run(main())


def count(app, name, reply=None):
    calls = []
    orig = getattr(app.mc.commands, name)

    async def spy(*a, **kw):
        calls.append((a, kw))
        return await (reply(*a, **kw) if reply else orig(*a, **kw))

    setattr(app.mc.commands, name, spy)
    return calls


async def never_acked(dst, msg, timestamp=None, attempt=0):
    return Event(EventType.MSG_SENT, {"type": 0, "expected_ack": os.urandom(4), "suggested_timeout": 50}, {})


def test_unanswered_dm_to_unrouted_contact_floods_at_most_twice():
    async def go(app, pilot):
        sends = count(app, "send_msg", never_acked)
        nora = BY_NAME["Nora 🌿"]
        assert nora["out_path_len"] < 0  # no stored route: every attempt floods
        await app.run_command("msg Nora hello?")
        for _ in range(120):
            await pilot.pause(0.25)
            win = app.find_window("dm:" + nora["public_key"])
            if win and win.recs and win.recs[-1].get("st") == "fail":
                break
        assert len(sends) == 2

    run(go, chat__dm_retries=8)  # even when asked for 8 attempts


def test_unanswered_dm_on_a_route_tries_four_times_and_floods_at_most_twice():
    async def go(app, pilot):
        sends = count(app, "send_msg", never_acked)
        ada = BY_NAME["ada 🦊"]
        await app.run_command("msg ada hello?")
        for _ in range(120):
            await pilot.pause(0.25)
            rec = next((r for r in app.find_window("dm:" + ada["public_key"]).recs if r.get("own") and r["text"] == "hello?"), None)
            if rec and rec.get("st") == "fail":
                break
        assert len(sends) <= 4
        assert [kw.get("attempt", a[3] if len(a) > 3 else 0) for a, kw in sends] == list(range(len(sends)))
        assert all(kw.get("attempt", 0) < 4 for a, kw in sends)

    run(go, chat__dm_retries=8, chat__flood_after=1)


def test_ping_spam_cannot_make_us_flood():
    from pathlib import Path

    import meshssi.plugins as plugins

    async def go(app, pilot):
        plugins.PLUGIN_DIR = Path(__file__).parent.parent / "examples" / "plugins"
        plugins.PluginManager.load_all(app.plugins)
        sends = count(app, "send_chan_msg")
        hiking = app.find_window("chan:#hiking")
        app.cfg["plugin.pingbot"]["channels"] = ["#hiking"]
        for i in range(40):  # 40 different people shouting !ping
            app.plugins.emit("channel_message", win=hiking, rec={"nick": f"spammer{i}", "text": "!ping", "hops": 1})
        await pilot.pause(1)
        assert len(sends) <= 3  # the automatic budget: 3 a minute

    run(go)


def test_away_replies_are_budgeted():
    async def go(app, pilot):
        sends = count(app, "send_msg")
        app.away = "out"
        for name in ("ada 🦊", "bramble", "Nora 🌿"):
            for _ in range(5):
                app.mc.queue_dm(name, "you there?", delay=0.01)
        await pilot.pause(3)
        replies = [a for a, kw in sends if a[1].startswith("[away]")]
        assert len({r[0]["adv_name"] for r in replies}) == len(replies) <= 3  # once per person, within budget

    run(go)


def test_scheduled_adverts_and_polls_respect_minimums():
    async def go(app, pilot):
        await pilot.pause(3)  # let the demo finish seeding its own dashboard before we count
        adverts = count(app, "send_advert")
        polls = count(app, "req_status_sync")
        app.cfg["device"]["advert_interval"] = 1  # asking for every minute
        app.last_advert = time.time() - 20 * 60  # 20 minutes ago: under the 30-minute floor
        await app.periodic()
        assert adverts == []
        app.last_advert = time.time() - 31 * 60
        await app.periodic()
        assert len(adverts) == 1 and adverts[0][1].get("flood") is False
        app.cfg["device"]["advert_flood"] = True
        app.last_advert = time.time() - 60 * 60  # an hour: fine for zero-hop, too soon for flood
        await app.periodic()
        assert len(adverts) == 1
        # watched repeaters: one poll per tick, none sooner than 15 minutes, never one without a route
        app.cfg["dashboard"]["interval"] = 1
        nora_like = dict(BY_NAME["Harbour Hill Rpt"], out_path_len=-1)
        app.mc.contacts[nora_like["public_key"]] = nora_like
        app.dash = {k: (0, None) for k in (BY_NAME["Ridgeline Rpt"]["public_key"], nora_like["public_key"])}
        for _ in range(3):
            await app.periodic()
            await pilot.pause(1.5)
        assert [a[0]["adv_name"] for a, kw in polls] == ["Ridgeline Rpt"]

    run(go)


def test_reconnect_storm_logs_into_rooms_once():
    async def go(app, pilot):
        room = BY_NAME["Makerspace Room"]
        app.cfg["rooms"][room["public_key"]] = "pw"
        logins = count(app, "send_login_sync")
        for _ in range(5):
            await app.auto_login_rooms()
        assert len(logins) == 1

    run(go)


def test_long_paste_asks_before_sending_many_packets():
    async def go(app, pilot):
        sends = count(app, "send_chan_msg")
        app.switch(app.windows.index(app.find_window("chan:#hiking")))
        await app.say(app.win, "word " * 200)  # ~7 packets
        assert sends == [] and "separate packets" in app.win.recs[-1]["text"]
        await app.say(app.win, "word " * 200)  # confirmed
        assert len(sends) >= 5

    run(go)
