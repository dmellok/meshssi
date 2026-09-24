import argparse
import asyncio
import logging

from . import __version__


def scan_ble() -> None:
    from bleak import BleakScanner

    async def run():
        print("Scanning for MeshCore radios over Bluetooth (5s)…")
        found = await BleakScanner.discover(timeout=5, return_adv=True)
        hits = [(d, adv) for d, adv in found.values() if (d.name or "").startswith("MeshCore")]
        if not hits:
            print("None found. Is Bluetooth on, and the radio's BLE enabled?")
        for d, adv in sorted(hits, key=lambda h: -h[1].rssi):
            print(f"  {d.name:<28} ble:{d.address}   rssi {adv.rssi} dBm")
        if hits:
            print("\nConnect with: meshssi ble:<address>   (set a PIN with /set connection.ble_pin 123456)")

    asyncio.run(run())


def main() -> None:
    from .config import Config

    cfg = Config()
    ap = argparse.ArgumentParser(prog="meshssi", description="irssi-style client for MeshCore companion radios")
    ap.add_argument("target", nargs="?", default=cfg.get("connection.target") or None,
                    help="host[:port] for WiFi/TCP (default port 5000), a serial device, or ble:<address>. "
                         "Remembers the last one used.")
    ap.add_argument("--serve", action="store_true",
                    help="run headless, sharing the radio with several clients on --listen (see README: daemon mode)")
    ap.add_argument("--listen", default=cfg.get("daemon.listen"), help="address for --serve (default %(default)s)")
    ap.add_argument("--demo", action="store_true", help="try meshssi against a simulated mesh, no radio needed")
    ap.add_argument("--scan", action="store_true", help="list MeshCore radios nearby over Bluetooth")
    ap.add_argument("--baud", type=int, default=115200, help="serial baud rate")
    ap.add_argument("--debug", action="store_true", help="write a debug log to meshssi-debug.log")
    ap.add_argument("--version", action="version", version=f"meshssi {__version__}")
    args = ap.parse_args()

    if args.debug:
        logging.basicConfig(filename="meshssi-debug.log", level=logging.DEBUG)
    elif not args.serve:
        # meshcore logs to stderr, which would scribble over the TUI
        logging.basicConfig(handlers=[logging.NullHandler()], level=logging.CRITICAL)

    if args.scan:
        scan_ble()
        return
    if args.demo:
        from .demo import DemoApp

        DemoApp().run()
        return
    if not args.target:
        ap.error("no target given, e.g. `meshssi 192.168.1.50`, `meshssi /dev/cu.usbmodem1101`, `meshssi ble:<addr>` "
                 "(or `meshssi --demo` / `meshssi --scan`)")
    if cfg.error:
        print(f"meshssi: {cfg.error}")
    cfg["connection"]["target"] = args.target
    cfg.save()  # does nothing if the file couldn't be parsed

    if args.serve:
        from .mux import run_daemon

        run_daemon(args.target, args.listen, baud=args.baud, pin=cfg.get("connection.ble_pin", ""))
        return

    from .app import MeshssiApp

    MeshssiApp(args.target, baud=args.baud, config=cfg).run()


if __name__ == "__main__":
    main()
