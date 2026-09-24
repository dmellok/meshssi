"""This radio: identity, radio settings, maintenance, backups."""

import asyncio
import json
import re
import time
import urllib.request
from pathlib import Path

from meshcore import EventType
from rich.markup import escape

from ..util import fmt_duration, write_private
from . import command


@command("info", "device", "/info", "Radio identity, firmware and settings", aliases=("sysinfo",))
async def c_info(app, args):
    ev = await app.cmd(app.mc.commands.send_appstart())
    si = app.self_info = dict(ev.payload)
    d = app.device_info
    app.echo(f"[bold]{escape(si['name'])}[/]  {si['public_key']}", markup=True)
    app.echo(f"  model     : {d.get('model')}  fw {d.get('ver')} (build {d.get('fw_build')}, protocol {d.get('fw ver')})")
    app.echo(f"  radio     : {si['radio_freq']:.3f} MHz  BW {si['radio_bw']:g} kHz  SF{si['radio_sf']}  CR{si['radio_cr']}")
    app.echo(f"  tx power  : {si['tx_power']} dBm (max {si['max_tx_power']})")
    app.echo(f"  location  : {si['adv_lat']:.5f}, {si['adv_lon']:.5f}  (shared in adverts: {'yes' if si['adv_loc_policy'] else 'no'})")
    app.echo(f"  contacts  : auto-add {'off' if si['manual_add_contacts'] else 'on'} · max {d.get('max_contacts')} · channels max {d.get('max_channels')}")
    app.echo(f"  multi-acks: {si['multi_acks']} · telemetry base/loc/env: {si['telemetry_mode_base']}/{si['telemetry_mode_loc']}/{si['telemetry_mode_env']}")
    app.echo(f"  repeat    : {'on' if d.get('repeat') else 'off'} · path hash mode {d.get('path_hash_mode')} · BLE pin {d.get('ble_pin') or 'none'}")
    try:
        t = await app.cmd(app.mc.commands.get_tuning())
        app.echo(f"  tuning    : rx delay {t.payload['rx_delay']} · airtime factor {t.payload['airtime_factor']}")
    except Exception:  # noqa: BLE001 - older firmware
        pass
    app.refresh_chrome()


@command("stats", "device", "/stats", "Uptime, battery, storage, noise floor, airtime and packet counters", aliases=("battery", "bat"))
async def c_stats(app, args):
    s = {}
    for fn in (app.mc.commands.get_stats_core, app.mc.commands.get_stats_radio, app.mc.commands.get_stats_packets, app.mc.commands.get_bat):
        ev = await app.cmd(fn())
        s.update(ev.payload)
    app.stats.update(s)
    up = s.get("uptime_secs", 0)
    app.echo(f"  uptime    : {fmt_duration(up)}   errors {s.get('errors')}   tx queue {s.get('queue_len')}")
    bat = f"{s['battery_mv'] / 1000:.2f} V" if s.get("battery_mv") else "not reported"
    app.echo(f"  battery   : {bat}   storage {s.get('used_kb')}/{s.get('total_kb')} KB")
    app.echo(f"  radio     : noise floor {s.get('noise_floor')} dBm · last RSSI {s.get('last_rssi')} dBm · last SNR {s.get('last_snr')} dB")
    duty = 100 * s.get("tx_air_secs", 0) / up if up else 0
    app.echo(f"  airtime   : tx {s.get('tx_air_secs')}s ({duty:.2f}% duty) · rx {s.get('rx_air_secs')}s")
    app.echo(f"  packets   : rx {s.get('recv')} (flood {s.get('flood_rx')}, direct {s.get('direct_rx')}, errors {s.get('recv_errors')}) · "
             f"tx {s.get('sent')} (flood {s.get('flood_tx')}, direct {s.get('direct_tx')})")
    app.refresh_statusbar()


@command("time", "device", "/time [sync]", "Show the radio clock, or set it from this computer")
async def c_time(app, args):
    if args == "sync":
        await app.cmd(app.mc.commands.set_time(int(time.time())))
        app.echo("Radio clock set from local time.", "ok")
    ev = await app.cmd(app.mc.commands.get_time())
    t = ev.payload["time"]
    app.echo(f"Radio time: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(t))} (drift {t - time.time():+.0f}s)")


@command("advert", "device", "/advert [flood] | /advert every <minutes|off>", "Send an advert now, or schedule them")
async def c_advert(app, args):
    if args.startswith("every"):
        val = args[5:].strip()
        if val not in ("off", "0", "") and not (val.isdigit() and int(val) >= 5):
            app.echo("Usage: /advert every <minutes, at least 5> or /advert every off", "error")
            return
        minutes = 0 if val in ("off", "0", "") else int(val)
        app.cfg["device"]["advert_interval"] = minutes
        app.cfg.save()
        app.echo(f"Automatic adverts: {'every ' + str(minutes) + ' min' if minutes else 'off'}.", "ok")
        return
    await app.cmd(app.mc.commands.send_advert(flood=args == "flood"))
    app.last_advert = time.time()
    app.echo(f"Sent {'flood' if args == 'flood' else 'zero-hop'} advert.", "ok")


@command("nick", "device", "/nick <name>", "Change this node's advertised name", aliases=("name",))
async def c_nick(app, args):
    if not args:
        app.echo(f"You are {app.my_name}.")
        return
    if len(args.encode()) > 31:
        app.echo("Node names are limited to 31 bytes.", "error")
        return
    await app.cmd(app.mc.commands.set_name(args))
    await app.cmd(app.mc.commands.send_appstart())
    app.status(f"You are now known as {args} (send /advert so others see it)", "ok")
    app.refresh_chrome()


@command("txpower", "device", "/txpower <dBm>", "Set transmit power")
async def c_txpower(app, args):
    top = app.self_info.get("max_tx_power", 22)
    if not args.lstrip("-").isdigit():
        app.echo(f"TX power is {app.self_info['tx_power']} dBm (max {top}).")
        return
    if not 1 <= int(args) <= top:
        app.echo(f"TX power must be between 1 and {top} dBm for this radio.", "error")
        return
    await app.cmd(app.mc.commands.set_tx_power(int(args)))
    await app.cmd(app.mc.commands.send_appstart())
    app.echo(f"TX power set to {args} dBm.", "ok")


@command("radio", "device", "/radio <MHz> <bw kHz> <sf> <cr> | /radio preset <name>", "Set LoRa parameters (must match your mesh!)")
async def c_radio(app, args):
    p = args.split()
    presets = app.cfg["radio_presets"]
    if p[:1] == ["preset"]:
        if len(p) != 2 or p[1] not in presets:
            app.echo("Presets (edit in config.toml [radio_presets]; check them against your local mesh first):")
            for name, (f, bw, sf, cr) in presets.items():
                app.echo(f"  {name:<12} {f:.3f} MHz  BW {bw:g}  SF{sf}  CR{cr}")
            return
        p = [str(v) for v in presets[p[1]]]
    if len(p) != 4:
        si = app.self_info
        app.echo(f"Radio: {si['radio_freq']:.3f} MHz, BW {si['radio_bw']:g} kHz, SF{si['radio_sf']}, CR{si['radio_cr']}. "
                 "Usage: /radio 916.575 62.5 7 8  or  /radio preset <name>")
        return
    try:
        freq, bw, sf, cr = float(p[0]), float(p[1]), int(p[2]), int(p[3])
    except ValueError:
        app.echo("Usage: /radio <MHz> <bw kHz> <sf> <cr>, e.g. /radio 916.575 62.5 7 8", "error")
        return
    if not (137 <= freq <= 1020 and bw in (7.8, 10.4, 15.6, 20.8, 31.25, 41.7, 62.5, 125.0, 250.0, 500.0)
            and 5 <= sf <= 12 and 5 <= cr <= 8):
        app.echo("Out of range: MHz 137-1020, BW one of 7.8/10.4/15.6/20.8/31.25/41.7/62.5/125/250/500, "
                 "SF 5-12, CR 5-8.", "error")
        return
    if not app.confirm(f"radio {freq} {bw} {sf} {cr} — a mismatch takes you off the mesh"):
        return
    await app.cmd(app.mc.commands.set_radio(freq, bw, sf, cr))
    app.echo("Radio parameters saved; they take effect after /reboot.", "ok")


@command("coords", "device", "/coords <lat> <lon> | /coords share on|off", "Set this node's location, or whether adverts include it")
async def c_coords(app, args):
    p = args.replace(",", " ").split()
    if p[:1] == ["share"] and len(p) == 2:
        share = app.on_off(p[1])
        if share is None:
            return
        await app.cmd(app.mc.commands.set_advert_loc_policy(1 if share else 0))
        await app.cmd(app.mc.commands.send_appstart())
        app.echo(f"Adverts {'include' if p[1] == 'on' else 'no longer include'} your location.", "ok")
        return
    try:
        lat, lon = float(p[0]), float(p[1])
        assert len(p) == 2 and -90 <= lat <= 90 and -180 <= lon <= 180
    except (ValueError, AssertionError, IndexError):
        app.echo("Usage: /coords <lat -90..90> <lon -180..180>, e.g. /coords -33.8688 151.2093", "error")
        return
    await app.cmd(app.mc.commands.set_coords(lat, lon))
    await app.cmd(app.mc.commands.send_appstart())
    app.echo("Location saved.", "ok")


async def set_device(app, key: str, val: str) -> None:
    c = app.mc.commands
    if not val:
        app.echo("Device settings: manualadd on|off · multiacks 0|1|2 · locpolicy 0|1 · pathhash 0|1|2 · "
                 "autoadd <bitmask> · telemetry base|loc|env 0|1|2", "error")
        return
    ranges = {"multiacks": (0, 2), "locpolicy": (0, 1), "pathhash": (0, 2)}
    if key in ranges and not (val.isdigit() and ranges[key][0] <= int(val) <= ranges[key][1]):
        app.echo(f"{key} must be {ranges[key][0]}-{ranges[key][1]}.", "error")
        return
    if key == "manualadd":
        on = app.on_off(val)
        if on is None:
            return
        await app.cmd(c.set_manual_add_contacts(on))
    elif key == "multiacks":
        await app.cmd(c.set_multi_acks(int(val)))
    elif key == "locpolicy":
        await app.cmd(c.set_advert_loc_policy(int(val)))
    elif key == "pathhash":
        await app.cmd(c.set_path_hash_mode(int(val)))
    elif key == "autoadd":
        await app.cmd(c.set_autoadd_config(int(val, 0)))
    elif key == "telemetry":
        which, _, mode = val.partition(" ")
        fn = {"base": c.set_telemetry_mode_base, "loc": c.set_telemetry_mode_loc, "env": c.set_telemetry_mode_env}.get(which)
        if not fn or mode not in ("0", "1", "2"):
            app.echo("Usage: /set telemetry base|loc|env 0|1|2 (0 deny, 1 contacts with permission, 2 everyone)", "error")
            return
        await app.cmd(fn(int(mode)))
    await app.cmd(c.send_appstart())
    app.echo(f"{key} = {val}", "ok")


@command("tuning", "device", "/tuning [rx_delay airtime_factor]", "Show or set the radio's rx delay and airtime factor")
async def c_tuning(app, args):
    p = args.split()
    if len(p) == 2:
        await app.cmd(app.mc.commands.set_tuning(int(p[0]), int(p[1])))
    ev = await app.cmd(app.mc.commands.get_tuning())
    app.echo(f"rx delay {ev.payload['rx_delay']} · airtime factor {ev.payload['airtime_factor']}", "ok" if p else "info")


@command("vars", "device", "/vars [name value]", "Firmware custom variables (e.g. GPS, sensors)")
async def c_vars(app, args):
    name, _, value = args.partition(" ")
    if name and value:
        await app.cmd(app.mc.commands.set_custom_var(name, value))
    ev = await app.cmd(app.mc.commands.get_custom_vars())
    if not ev.payload:
        app.echo("This firmware exposes no custom variables.")
    for k, v in ev.payload.items():
        app.echo(f"  {k} = {v}")


@command("pin", "device", "/pin <6 digits|0>", "Set the Bluetooth pairing PIN (0 = random each boot)")
async def c_pin(app, args):
    if args != "0" and not (len(args) == 6 and args.isdigit() and args[0] != "0"):
        app.echo("Usage: /pin <6 digits, not starting with 0>, or /pin 0 for a random PIN each boot", "error")
        return
    await app.cmd(app.mc.commands.set_devicepin(int(args)))
    app.echo("BLE PIN saved (takes effect after reboot).", "ok")


@command("cli", "device", "/cli <command>", "Run a raw firmware CLI command on this radio")
async def c_cli(app, args):
    ev = await app.cmd(app.mc.commands.run_cli_command(args))
    app.echo(ev.payload.get("text", str(ev.payload)))


@command("refresh", "device", "/refresh", "Reload identity, contacts and channels from the radio", aliases=("rehash",))
async def c_refresh(app, args):
    await app.cmd(app.mc.commands.send_appstart())
    await app.refresh_contacts()
    await app.load_channels()
    app.refresh_chrome()
    app.echo("Refreshed.", "ok")


@command("reboot", "device", "/reboot", "Reboot the radio (asks for confirmation)")
async def c_reboot(app, args):
    if app.confirm("reboot the radio"):
        try:
            await asyncio.wait_for(app.mc.commands.reboot(), 3)
        except Exception:  # noqa: BLE001 - it usually drops the link before replying
            pass
        app.status("Reboot sent; will reconnect when it's back.")


@command("keybackup", "device", "/keybackup <file>", "Save this node's private key (identity) to a file — keep it secret")
async def c_keybackup(app, args):
    if not args:
        app.echo("Usage: /keybackup ~/meshcore-identity.json", "error")
        return
    if not app.confirm(f"write your node's PRIVATE key to {args}"):
        return
    async with app.io:
        ev = await app.mc.commands.export_private_key()
    if ev.type == EventType.DISABLED:
        app.echo("This firmware has private key export disabled.", "error")
        return
    if ev.type != EventType.PRIVATE_KEY:
        app.echo(f"Export failed: {ev.payload}", "error")
        return
    path = Path(args).expanduser()
    try:
        write_private(path, json.dumps({"name": app.my_name, "public_key": app.self_info["public_key"],
                                        "private_key": ev.payload["private_key"].hex(),
                                        "exported": time.strftime("%Y-%m-%d %H:%M:%S")}, indent=2))
    except FileExistsError:
        app.echo(f"{path} already exists; choose a new file name (an old backup may be the only copy).", "error")
        return
    app.echo(f"Identity saved to {path} (mode 600). Anyone with this file can impersonate your node.", "ok")


@command("keyrestore", "device", "/keyrestore <file>", "Load a private key from /keybackup onto this radio (replaces its identity)")
async def c_keyrestore(app, args):
    path = Path(args).expanduser()
    if not path.exists():
        app.echo("Usage: /keyrestore <file from /keybackup>", "error")
        return
    data = json.loads(path.read_text())
    if not app.confirm(f"replace this radio's identity with {data.get('name')} ({data.get('public_key', '')[:12]})"):
        return
    await app.cmd(app.mc.commands.import_private_key(bytes.fromhex(data["private_key"])))
    app.echo("Identity restored. /reboot for it to take effect.", "ok")


@command("factoryreset", "device", "/factoryreset <node name>", "Erase everything on the radio (type its exact name, twice)")
async def c_factoryreset(app, args):
    if args != app.my_name:
        app.echo(f"This ERASES contacts, channels, keys and settings. To confirm, type: /factoryreset {app.my_name}", "error")
        return
    if not app.confirm(f"factory reset {app.my_name} — everything on it is erased"):
        return
    token = await app.mc.commands.request_factory_reset()
    async with app.io:
        await app.mc.commands.confirm_factory_reset(token)
    app.status("Factory reset sent. The radio will restart with a fresh identity.", "error")


@command("fwcheck", "device", "/fwcheck", "Compare the radio's firmware with the latest MeshCore companion release (asks GitHub)")
async def c_fwcheck(app, args):
    def fetch():
        req = urllib.request.Request("https://api.github.com/repos/meshcore-dev/MeshCore/releases?per_page=40",
                                     headers={"Accept": "application/vnd.github+json", "User-Agent": "meshssi"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.load(r)

    releases = await asyncio.to_thread(fetch)
    mine = app.device_info.get("ver", "?")

    def ver(tag):
        m = re.search(r"(\d+)\.(\d+)\.(\d+)", tag)
        return tuple(int(x) for x in m.groups()) if m else None

    companion = [r for r in releases if "companion" in r.get("tag_name", "").lower() and not r.get("prerelease")]
    pool = companion or [r for r in releases if not r.get("prerelease")]
    if not pool:
        app.echo("Couldn't find any releases.", "error")
        return
    latest = max(pool, key=lambda r: ver(r["tag_name"]) or (0,))
    lv, mv = ver(latest["tag_name"]), ver(mine)
    if mv and lv and mv >= lv:
        app.echo(f"Firmware {mine} is up to date (latest {latest['tag_name']}).", "ok")
    else:
        app.echo(f"Firmware {mine} → {latest['tag_name']} available ({latest.get('published_at', '')[:10]}): {latest.get('html_url')}")
        app.echo("Flash it with the web flasher: https://flasher.meshcore.co.uk", "dim")
