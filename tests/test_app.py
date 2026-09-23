"""Drive the real TUI against the simulated mesh."""

import asyncio
import random

from meshssi.commands import COMMANDS
from meshssi.demo import BY_NAME, DemoApp

READ_ONLY = ["/help", "/help mesh", "/info", "/stats", "/time", "/contacts", "/whois ada 🦊", "/heard", "/channels",
             "/key #mesh-dev", "/uri", "/qr", "/pending", "/rf stats", "/map", "/graphs", "/dash", "/rf", "/window list",
             "/set", "/alias", "/ignore", "/hilight", "/plugins", "/notify", "/radio preset", "/vars", "/tuning", "/scope"]


async def boot(pilot, app):
    for _ in range(80):
        await pilot.pause(0.1)
        if app.connected and app.find_window("chan:Public") and app.find_window("chan:Public").recs:
            return
    raise AssertionError("demo never connected")


def errors(app):
    return [f"{w.name}: {r['text']}" for w in app.windows for r in w.recs if r.get("lvl") == "error"]


def test_every_read_only_command_runs_clean():
    async def run():
        random.seed(1)
        app = DemoApp(live=False)
        async with app.run_test(size=(140, 40)) as pilot:
            await boot(pilot, app)
            for cmd in READ_ONLY:
                await app.run_command(cmd[1:])
                await pilot.pause(0.05)
            assert errors(app) == []
            await app.action_quit()

    asyncio.run(run())


def test_messaging_acks_chunks_heard_by_and_aliases():
    async def run():
        random.seed(3)
        app = DemoApp(live=False)
        app.cfg["chat"]["dm_retries"] = 6  # the fake drops 20% of acks; retries should cover that
        async with app.run_test(size=(140, 40)) as pilot:
            await boot(pilot, app)
            await app.run_command("msg bramble " + "long " * 70)
            await app.run_command("msg #hiking summit :thumbs_up:")
            await app.run_command("alias hk /msg #hiking $*")
            await app.run_command("hk from the alias")
            dm = app.find_window("dm:" + BY_NAME["bramble"]["public_key"])
            for _ in range(120):  # retries wait out missing acks, so give delivery time to settle
                await pilot.pause(0.5)
                own = [r for r in dm.recs if r.get("own")]
                if all(r.get("st") != "pending" for r in own):
                    break
            assert len(own) == 3 and all(len(r["text"].encode()) <= 150 for r in own)
            assert all(r["st"] == "ok" for r in own), [r["st"] for r in own]
            hike = [r for r in app.find_window("chan:#hiking").recs if r.get("own")]
            assert [r["text"] for r in hike] == ["summit 👍", "from the alias"]
            assert all(r.get("heard", 0) >= 1 for r in hike)
            assert errors(app) == []
            await app.action_quit()

    asyncio.run(run())


def test_trace_and_remote_admin():
    async def run():
        app = DemoApp(live=False)
        async with app.run_test(size=(140, 40)) as pilot:
            await boot(pilot, app)
            for cmd in ("trace Ridgeline Rpt", "login Ridgeline Rpt pw", "rstatus Ridgeline Rpt", "neighbours Ridgeline Rpt",
                        "acl Ridgeline Rpt", "owner Ridgeline Rpt", "regions Ridgeline Rpt", "watch Harbour Hill Rpt",
                        "rcmd Ridgeline Rpt ver"):
                await app.run_command(cmd)
            await pilot.pause(3)
            text = "\n".join(r.get("text", "") for w in app.windows for r in w.recs)
            assert "→ Ridgeline Rpt" in text and "(admin)" in text and "22d" in text
            assert "Blue Mtns Rpt" in text  # neighbour resolved by name from adverts heard over the air
            assert "v1.17.1 (Build" in text  # CLI reply routed into the repeater's window
            assert errors(app) == []
            await app.action_quit()

    asyncio.run(run())


def test_all_commands_have_help():
    for name, (fn, cat, usage, text) in COMMANDS.items():
        assert usage.startswith("/") and text, name


def test_example_plugins_load_and_pingbot_answers(monkeypatch):
    from pathlib import Path

    import meshssi.plugins as plugins

    monkeypatch.setattr(plugins, "PLUGIN_DIR", Path(__file__).parent.parent / "examples" / "plugins")

    async def run():
        app = DemoApp(live=False)
        app.plugins.load_all = plugins.PluginManager.load_all.__get__(app.plugins)  # the demo normally skips plugins
        async with app.run_test(size=(140, 40)) as pilot:
            await boot(pilot, app)
            assert sorted(app.plugins.loaded) == ["keywords", "pingbot", "webhook"]
            assert "pingbot" in COMMANDS
            app.mc.queue_dm("ada 🦊", "!ping", delay=0.1)
            for _ in range(40):
                await pilot.pause(0.25)
                dm = app.find_window("dm:" + BY_NAME["ada 🦊"]["public_key"])
                if any(r.get("own") and r["text"].startswith("pong") for r in dm.recs):
                    break
            else:
                raise AssertionError("pingbot never answered")
            await app.action_quit()

    asyncio.run(run())
