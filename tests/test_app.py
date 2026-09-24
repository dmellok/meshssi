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


def test_completion_and_lookup_ignore_emoji():
    async def run():
        app = DemoApp(live=False)
        async with app.run_test(size=(140, 40)) as pilot:
            await boot(pilot, app)
            app.switch(0)

            def complete(line):
                start, cands, _ = app.completions(line)
                return [line[:start] + c for c in cands]

            assert complete("/msg nora") == ["/msg Nora 🌿 "]
            assert complete("/msg herb") == ["/msg Nora 🌿 "]  # 🌿 is :herb:
            assert complete("/whois fox") == ["/whois ada 🦊 "]  # 🦊 is :fox_face:
            assert "/trace Harbour Hill Rpt " in complete("/trace hill")  # any word in the name
            c, rest = app.split_target("nora hello there")
            assert c["adv_name"] == "Nora 🌿" and rest == "hello there"
            c, rest = app.split_target("ada fox_face hi")
            assert c["adv_name"] == "ada 🦊" and rest == "hi"
            assert app.find_contact("herb")["adv_name"] == "Nora 🌿"
            app.switch(app.windows.index(app.find_window("chan:Public")))
            assert complete("nor") == ["@[Nora 🌿] "]
            await app.action_quit()

    asyncio.run(run())


def test_long_lines_wrap_with_hanging_indent():
    async def run():
        app = DemoApp(live=False)
        async with app.run_test(size=(140, 40)) as pilot:
            await boot(pilot, app)
            rec = {"k": "msg", "nick": "Bin Chicken 🦩", "text": "word " * 40, "t": 0, "hops": 1, "snr": 2.0}
            lines = app.wrapped(rec, 60).plain.split("\n")
            from rich.cells import cell_len

            indent = cell_len(lines[0][: lines[0].index("> ") + 2])  # the emoji is two cells wide
            assert len(lines) > 2
            for cont in lines[1:]:
                assert cont[:indent] == " " * indent and cont[indent] != " "  # text starts exactly under the message
                assert cell_len(cont) <= 60
            note = {"k": "notice", "text": "x " * 60, "lvl": "info", "t": 0}
            nlines = app.wrapped(note, 50).plain.split("\n")
            assert all(ln.startswith(" " * 10) for ln in nlines[1:])
            await app.action_quit()

    asyncio.run(run())


def test_setperm_is_strict_and_confirmed():
    async def run():
        app = DemoApp(live=False)
        async with app.run_test(size=(140, 40)) as pilot:
            await boot(pilot, app)
            sent = []
            orig = app.mc.commands.send_cmd

            async def spy(dst, cmd, *a, **kw):
                sent.append(cmd)
                return await orig(dst, cmd, *a, **kw)

            app.mc.commands.send_cmd = spy
            rpt = "Ridgeline Rpt"
            ada = BY_NAME["ada 🦊"]["public_key"]

            def last():
                return app.win.recs[-1]["text"] if app.win.recs else ""

            await app.run_command(f"setperm {rpt} ad admin")  # partial name: refused, nothing sent
            await app.run_command(f"setperm {rpt} adaa admin")  # typo: refused
            await app.run_command(f"setperm {rpt} {ada[:10]} admin")  # prefix can't grant access
            assert sent == []
            await app.run_command(f"setperm {rpt} ada admin")  # emoji-less exact name: asks first
            assert sent == [] and "ADMIN" in last() and ada[:12] in last()
            await app.run_command(f"setperm {rpt} ada admin")  # repeated: sent with the full key
            assert sent == [f"setperm {ada} 3"]
            await app.run_command(f"setperm {rpt} {ada[:10]} guest")
            await app.run_command(f"setperm {rpt} {ada[:10]} guest")  # removal by prefix, confirmed
            assert sent[-1] == f"setperm {ada} 0"
            await app.action_quit()

    asyncio.run(run())


def test_prompt_counter_wrapping_history_and_keys():
    async def run():
        app = DemoApp(live=False)
        async with app.run_test(size=(100, 30)) as pilot:
            await boot(pilot, app)
            app.switch(app.windows.index(app.find_window("chan:#hiking")))
            inp = app.query_one("#input")
            counter = app.query_one("#counter")
            limit = 150 - len(app.my_name.encode()) - 2
            await pilot.press(*"hello")
            await pilot.pause(0.1)
            assert str(counter.render()) == f"5/{limit}"
            inp.value = "x" * (limit - 5)
            await pilot.pause(0.1)
            assert inp.size.height > 1  # the input grew instead of scrolling sideways
            assert str(counter.render()).startswith(f"{limit - 5}/")
            inp.value = "word " * 40
            await pilot.pause(0.1)
            assert "2 msgs" in str(counter.render())
            inp.value = "/help"
            await pilot.pause(0.1)
            assert str(counter.render()) == ""  # commands aren't messages
            inp.value = "on the summit"
            await pilot.press("enter")
            await pilot.pause(0.3)
            assert inp.value == "" and app.find_window("chan:#hiking").recs[-1]["text"] == "on the summit"
            await pilot.press("up")
            assert inp.value == "on the summit"
            await pilot.press("down")
            assert inp.value == ""
            await pilot.press(*"/qu", "tab")
            assert inp.value in ("/query ", "/quit ")
            inp.value = ""
            await app.run_command("map")
            await pilot.press("+", "right")
            await pilot.pause(0.2)
            assert app.map_state.user is not None and inp.value == ""
            await app.action_quit()

    asyncio.run(run())
