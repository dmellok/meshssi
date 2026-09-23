import argparse
import logging

from .store import load_config, save_config


def main() -> None:
    cfg = load_config()
    ap = argparse.ArgumentParser(prog="meshssi", description="irssi-style client for MeshCore companion radios")
    ap.add_argument(
        "target",
        nargs="?",
        default=cfg.get("target"),
        help="host[:port] for WiFi/TCP companions (default port 5000), or a serial device path. "
        "Remembers the last one used.",
    )
    ap.add_argument("--baud", type=int, default=115200, help="serial baud rate")
    ap.add_argument("--debug", action="store_true", help="write meshcore debug log to meshssi-debug.log")
    args = ap.parse_args()

    if not args.target:
        ap.error("no target given, e.g. `meshssi 192.168.1.50` or `meshssi /dev/cu.usbmodem1101`")
    cfg["target"] = args.target
    save_config(cfg)

    # meshcore logs to stderr, which would scribble over the TUI
    if args.debug:
        logging.basicConfig(filename="meshssi-debug.log", level=logging.DEBUG)
    else:
        logging.basicConfig(handlers=[logging.NullHandler()], level=logging.CRITICAL)

    from .app import MeshssiApp

    MeshssiApp(args.target, baud=args.baud).run()


if __name__ == "__main__":
    main()
