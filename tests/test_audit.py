"""Regression tests for the issues found in the 0.3 audit: wrong targets, remote input, data safety."""

import asyncio
import os
import stat

import pytest

from meshssi.config import Config
from meshssi.demo import BY_NAME, DemoApp, contact, key
from meshssi.store import Store
from meshssi.util import clean, split_utf8, write_private

from .test_app import boot


def run(coro_fn):
    async def main():
        app = DemoApp(live=False)
        async with app.run_test(size=(140, 40)) as pilot:
            await boot(pilot, app)
            await coro_fn(app, pilot)
            await app.action_quit()

    asyncio.run(main())


def spy(app, name):
    calls = []
    orig = getattr(app.mc.commands, name)

    async def wrapper(*a, **kw):
        calls.append(a)
        return await orig(*a, **kw)

    setattr(app.mc.commands, name, wrapper)
    return calls


def add(app, *nodes):
    for n in nodes:
        app.mc.contacts[n["public_key"]] = n


def errors(app):
    return [r["text"] for w in app.windows for r in w.recs if r.get("lvl") == "error"]


# ── remote input can't reach the terminal ─────────────────────────────────────────────────────────────
def test_escape_sequences_and_newlines_are_stripped():
    assert clean("\x1b]52;c;SGk=\x1b\\ hi\nthere\x9b") == "]52;c;SGk=\\ hi there"

    async def go(app, pilot):
        app.mc.queue_chan("Public", "x\x1b[2J", "\x1b]0;PWNED\x07 hi\n10:05 <bramble> fake", delay=0.05)
        await pilot.pause(0.6)
        pub = app.find_window("chan:Public")
        line = app.wrapped(pub.recs[-1], 200).plain
        assert "\x1b" not in line and "\n" not in line and "hi" in line

    run(go)


# ── the right target, or none ─────────────────────────────────────────────────────────────────────────
def test_ambiguous_and_near_miss_names_are_refused():
    async def go(app, pilot):
        add(app, contact("Pat 🚲", 1, 1, 60), contact("Pat 🌮", 1, 1, 60))
        sent = spy(app, "send_msg")
        await app.run_command("msg Pat hi there")
        assert sent == [] and "could be" in app.resolve_error or "matches" in app.resolve_error
        add(app, contact("Kim", 1, 1, 60), contact("Kim Smith", 1, 1, 60))
        await app.run_command("msg Kim Smiht are you coming?")  # typo of the longer name
        await app.run_command("msg ora hello")  # mid-word: not Nora
        await pilot.pause(0.2)
        assert sent == []
        await app.run_command('msg "Kim" are you coming?')  # quoting is explicit
        await pilot.pause(0.5)
        assert sent and sent[-1][0]["adv_name"] == "Kim"

    run(go)


def test_msg_public_prefers_asking_over_broadcasting():
    async def go(app, pilot):
        add(app, contact("Public Works", 1, 1, 60))
        chan = spy(app, "send_chan_msg")
        await app.run_command("msg Public Works are closed today")
        assert chan == [] and any("Ambiguous" in e for e in errors(app))

    run(go)


def test_login_typo_never_sends_password_to_open_window():
    async def go(app, pilot):
        app.switch(app.windows.index(app.query_window(BY_NAME["ada 🦊"])))
        logins = spy(app, "send_login_sync")
        await app.run_command("login Ridgline hunter2")  # typo of Ridgeline Rpt
        await app.run_command("login")  # in a chat DM: not a repeater
        assert logins == []

    run(go)


def test_rcmd_in_a_node_window_sends_the_whole_line_there():
    async def go(app, pilot):
        add(app, contact("Sunset Rpt", 2, 1, 60))
        rpt = BY_NAME["Ridgeline Rpt"]
        app.switch(app.windows.index(app.query_window(rpt)))
        cmds = spy(app, "send_cmd")
        await app.run_command("rcmd set tx 22")
        await app.run_command("rcmd password s3cret")
        assert [(c[0]["adv_name"], c[1]) for c in cmds] == [("Ridgeline Rpt", "set tx 22"), ("Ridgeline Rpt", "password s3cret")]
        shown = [r["text"] for r in app.win.recs]
        assert not any("s3cret" in t for t in shown)

    run(go)


def test_destructive_channel_and_contact_commands():
    async def go(app, pilot):
        chans = spy(app, "set_channel")
        await app.run_command("join #secret 00112233445566778899aabbccddeeff")  # key would be ignored
        await app.run_command("part #mesh-dev")  # asks first
        assert chans == []
        await app.run_command("part #mesh-dev")
        assert chans and chans[-1][0] == 1
        adds = spy(app, "add_contact")
        app.mc.pending_contacts = {key("a"): contact("aa", 1, 1, 1), key("b"): contact("ab", 1, 1, 1)}
        await app.run_command("accept")  # empty: usage, not "everyone"
        await app.run_command("accept a")  # two matches: asks first
        assert adds == []

    run(go)


def test_device_inputs_are_validated():
    async def go(app, pilot):
        pins = spy(app, "set_devicepin")
        manual = spy(app, "set_manual_add_contacts")
        power = spy(app, "set_tx_power")
        for c in ("pin 012345", "pin 12", "set manualadd of", "txpower 30", "coords 151.2 -33.8", "advert every -5"):
            await app.run_command(c)
        assert pins == manual == power == []
        assert app.cfg.get("device.advert_interval") == 0
        await app.run_command("pin 123456")
        assert pins == [(123456,)]

    run(go)


def test_alias_wrapping_its_own_command_and_lastlog_not_matching_itself():
    async def go(app, pilot):
        chan = spy(app, "send_chan_msg")
        await app.run_command("alias msg /msg #hiking")
        await app.run_command("msg hello")
        assert [c[1] for c in chan] == ["hello"]
        await app.run_command("lastlog zebra")
        await app.run_command("lastlog zebra")
        assert "0 match" in app.win.recs[-1]["text"]

    run(go)


def test_channel_reload_keeps_you_in_the_same_window():
    async def go(app, pilot):
        ada = app.query_window(BY_NAME["ada 🦊"])
        app.switch(app.windows.index(ada))
        import meshssi.demo as demo

        old = dict(demo.CHANNELS)
        try:
            demo.CHANNELS.pop(1)  # #mesh-dev deleted elsewhere
            demo.CHANNELS[2] = ("#renamed", None)  # and #hiking's slot reused
            await app.load_channels()
        finally:
            demo.CHANNELS.clear()
            demo.CHANNELS.update(old)
        assert app.win is ada
        names = [w.name for w in app.windows if w.kind == "channel"]
        assert "#mesh-dev" not in names and "#hiking" not in names and "#renamed" in names

    run(go)


def test_late_ack_turns_a_failed_dm_into_delivered():
    async def go(app, pilot):
        app.cfg["chat"]["dm_retries"] = 1
        codes = []
        orig = app.mc.commands.send_msg

        async def no_ack(dst, msg, timestamp=None, attempt=0):
            from meshcore.events import Event, EventType

            code = os.urandom(4)
            codes.append(code.hex())
            return Event(EventType.MSG_SENT, {"type": 0, "expected_ack": code, "suggested_timeout": 100}, {})

        app.mc.commands.send_msg = no_ack
        await app.run_command("msg bramble anyone there?")
        for _ in range(40):
            await pilot.pause(0.25)
            rec = next(r for r in app.find_window("dm:" + BY_NAME["bramble"]["public_key"]).recs if r.get("own"))
            if rec.get("st") == "fail":
                break
        assert rec["st"] == "fail"
        from meshcore.events import Event, EventType

        await app.on_ack(Event(EventType.ACK, {"code": codes[-1]}, {"code": codes[-1]}))
        assert rec["st"] == "ok" and not app.acks
        app.mc.commands.send_msg = orig

    run(go)


def test_background_errors_do_not_kill_the_app():
    async def go(app, pilot):
        async def boom():
            raise RuntimeError("kaboom")

        app.run_worker(boom())
        await pilot.pause(0.3)
        assert app.is_running and any("kaboom" in r["text"] for r in app.windows[0].recs)

    run(go)


# ── text handling ─────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("text,limit", [(" " * 200 + "hi", 148), ("🐢🐢", 3), ("x" * 50, 0), ("x" * 50, -5)])
def test_split_utf8_never_hangs_or_sends_empty(text, limit):
    chunks = split_utf8(text, limit)
    assert chunks and all(c.strip() for c in chunks)


def test_split_utf8_keeps_emoji_sequences_whole():
    family = "👨‍👩‍👧"
    chunks = split_utf8("a" * 146 + family, 150)
    assert chunks[-1] == family


# ── data on disk ──────────────────────────────────────────────────────────────────────────────────────
def test_unreadable_config_is_never_overwritten(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[rooms]\n"Club" = "hunter2"\n[ui\nbroken = "')
    before = path.read_text()
    cfg = Config(path)
    assert cfg.error
    cfg["connection"]["target"] = "10.0.0.5"
    cfg.save()
    assert path.read_text() == before


def test_config_round_trips_awkward_keys_and_is_private(tmp_path):
    path = tmp_path / "config.toml"
    cfg = Config(path)
    cfg["rooms"]["Café ルーム"] = "pw\x7f"
    cfg["plugin.demo"]["opts"] = {"a": 1}
    cfg.save()
    again = Config(path)
    assert not again.error and again["rooms"]["Café ルーム"] == "pw\x7f" and again["plugin.demo"]["opts"] == {"a": 1}
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert again.set("chat.highlights", "[1, 2]") == ["1", "2"]
    assert again.set("plugin.demo.level", "3") == "3"


def test_store_names_are_unique_per_window(tmp_path):
    st = Store("abcdef123456", root=tmp_path)
    for k in ("chan:#vic", "chan:@vic", "chan:🦊", "chan:🐢", "chan:#Vic"):
        st.append(k, {"k": "msg", "id": k, "text": k})
    for k in ("chan:#vic", "chan:@vic", "chan:🦊", "chan:🐢", "chan:#Vic"):
        assert [r["text"] for r in st.load(k)] == [k]


def test_private_files_refuse_to_overwrite(tmp_path):
    p = tmp_path / "backup.json"
    write_private(p, "secret")
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
    with pytest.raises(FileExistsError):
        write_private(p, "other")


def test_mux_ignores_callbacks_from_replaced_connections():
    from meshssi.mux import Mux

    async def go():
        mux = Mux(lambda: None, "127.0.0.1", 0)
        old, new = object(), object()
        mux.transport = new
        mux.upstream_up.set()
        await mux._lost_callback(old)("tcp_disconnect")
        assert mux.upstream_up.is_set()
        await mux._lost_callback(new)("tcp_disconnect")
        assert not mux.upstream_up.is_set()

    asyncio.run(go())
