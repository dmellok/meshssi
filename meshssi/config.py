"""User settings in ~/.config/meshssi/config.toml (created with defaults on first run)."""

import copy
import json
import os
import re
import tomllib
from pathlib import Path
from typing import Any

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "meshssi"
CONFIG_PATH = CONFIG_DIR / "config.toml"
PLUGIN_DIR = CONFIG_DIR / "plugins"

DEFAULTS: dict[str, dict[str, Any]] = {
    "connection": {
        "target": "",  # host[:port], /dev/tty..., or ble:<address>
        "ble_pin": "",
    },
    "ui": {
        "theme": "irssi",
        "layout": "classic",  # classic (irssi) or easy (window list, toolbar, key hints)
        "hints": True,  # show matching commands and arguments above the input while typing /
        "timestamp_format": "%H:%M",
        "nicklist": True,
        "show_hops": True,  # hop-count column before the nick
        "show_snr": False,  # SNR tag after received messages
        "show_signal": True,  # heard-by / round-trip tags after your own messages
        "show_paths": False,  # repeater path after channel messages (needs rx log)
        "scrollback": 2000,
    },
    "chat": {
        "highlights": [],  # extra words that highlight a channel line
        "ignores": [],  # nick glob patterns to hide
        "away_message": "",
        "dm_retries": 3,  # attempts before giving up on a DM
        "flood_after": 2,  # attempts before resetting the route and flooding
        "emoji_shortcodes": True,  # :thumbs_up: -> 👍 when sending
    },
    "notify": {
        "desktop": True,  # macOS / Linux notification for DMs and mentions
        "bell": True,
        "only_when_unfocused": True,
    },
    "device": {
        "auto_time_sync": True,  # fix the radio clock on connect if it drifted
        "advert_interval": 0,  # minutes between automatic adverts, 0 = off
        "advert_flood": False,
    },
    "map": {
        "basemap": True,  # OpenStreetMap background (downloaded tiles are cached for offline use)
        "style": "braille",  # braille, or "dots" if your font lacks braille characters
        "tiles": "https://tiles.openfreemap.org/planet",  # TileJSON URL or a {z}/{x}/{y} template (MVT)
    },
    "dashboard": {
        "interval": 10,  # minutes between status polls of /watch'ed repeaters
        "watch": [],  # repeater names or key prefixes
    },
    "daemon": {
        "listen": "127.0.0.1:5001",
    },
    "rooms": {},  # room name -> password, for auto-login
    "aliases": {
        "j": "/join",
        "ll": "/lastlog",
        "wii": "/whois",
    },
    "radio_presets": {
        # name = [MHz, bandwidth kHz, spreading factor, coding rate]. Check these against your local mesh.
        "au": [915.800, 250.0, 10, 5],
        "au-narrow": [916.575, 62.5, 7, 8],
        "eu": [869.525, 250.0, 11, 5],
        "eu-narrow": [869.618, 62.5, 8, 8],
        "nz": [917.375, 250.0, 11, 5],
        "nz-narrow": [917.375, 62.5, 7, 5],
        "us": [910.525, 62.5, 7, 5],
    },
}


BARE_KEY = re.compile(r"^[A-Za-z0-9_-]+$")


def _toml_str(v: str) -> str:
    out = json.dumps(v, ensure_ascii=False)
    return out.replace("\x7f", "\\u007f")  # TOML forbids a raw DEL


def _toml_key(k: str) -> str:
    return k if BARE_KEY.match(k) else _toml_str(k)


def _toml_value(v: Any) -> str:
    import datetime as _dt

    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        return _toml_str(v)
    if isinstance(v, (_dt.date, _dt.time)):
        return v.isoformat()
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{" + ", ".join(f"{_toml_key(k)} = {_toml_value(x)}" for k, x in v.items()) + "}"
    raise TypeError(f"can't write {type(v).__name__} to TOML")


def dumps(data: dict[str, dict[str, Any]]) -> str:
    out = ["# meshssi settings. Edit here or with /set section.key value inside the app.\n"]
    for section, values in data.items():
        out.append("[" + ".".join(_toml_key(part) for part in section.split(".")) + "]")
        for k, v in values.items():
            out.append(f"{_toml_key(k)} = {_toml_value(v)}")
        out.append("")
    return "\n".join(out)


class Config:
    def __init__(self, path: Path = CONFIG_PATH):
        self.path = path
        self.data = copy.deepcopy(DEFAULTS)
        self.error = ""
        if path.exists():
            try:
                loaded = tomllib.loads(path.read_text(encoding="utf-8"))
            except (tomllib.TOMLDecodeError, UnicodeDecodeError) as e:
                self.error = f"{path} couldn't be read ({e}); using defaults for now and NOT saving over it — fix it by hand"
                loaded = {}
            for section, values in loaded.items():
                if not isinstance(values, dict):
                    continue
                # [plugin.name] reads back as nested tables; keep them as flat "plugin.name" sections
                flat = {k: v for k, v in values.items() if not (isinstance(v, dict) and section == "plugin")}
                if flat or section != "plugin":
                    self.data.setdefault(section, {}).update(flat)
                for k, v in values.items():
                    if isinstance(v, dict) and section == "plugin":
                        self.data.setdefault(f"plugin.{k}", {}).update(v)
        self._migrate_json()
        if not path.exists():
            self.save()

    def _migrate_json(self) -> None:
        old = self.path.with_name("config.json")
        if old.exists() and not self.data["connection"]["target"]:
            try:
                self.data["connection"]["target"] = json.loads(old.read_text()).get("target", "")
            except (OSError, json.JSONDecodeError):
                pass

    def save(self) -> None:
        """Write atomically, readable only by you (it can hold room passwords), and never over a file we
        couldn't parse: that would silently replace the user's settings with defaults."""
        if self.error:
            return
        text = dumps(self.data)
        tomllib.loads(text)  # refuse to write anything we couldn't read back
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        tmp = self.path.with_name(self.path.name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)

    def __getitem__(self, section: str) -> dict[str, Any]:
        return self.data.setdefault(section, {})

    def get(self, dotted: str, default: Any = None) -> Any:
        section, _, key = dotted.rpartition(".")
        return self.data.get(section, {}).get(key, default)

    def set(self, dotted: str, raw: str) -> Any:
        """Set section.key from a string typed at the prompt, coercing to the existing type."""
        section, _, key = dotted.rpartition(".")
        if section == "rooms":
            raise KeyError("room passwords are set with /room <room> <password> -save")
        if not key:
            raise KeyError("use section.key, e.g. chat.dm_retries")
        current = self.data.get(section, {}).get(key)
        free_form = section in ("aliases", "radio_presets") or section.startswith("plugin.")
        if not free_form and (section not in self.data or key not in self.data[section]):
            raise KeyError(f"unknown setting {dotted} — /set lists them")
        value = parse_value(raw, current)
        self.data.setdefault(section, {})
        if section == "radio_presets" and not (isinstance(value, list) and len(value) == 4):
            raise ValueError("a preset is [MHz, bandwidth kHz, SF, CR], e.g. [916.575, 62.5, 7, 8]")
        if section == "radio_presets":
            value = [float(value[0]), float(value[1]), int(value[2]), int(value[3])]
        self.data[section][key] = value
        self.save()
        return value

    def unset(self, dotted: str) -> None:
        section, _, key = dotted.rpartition(".")
        self.data.get(section, {}).pop(key, None)
        if key in DEFAULTS.get(section, {}):
            self.data[section][key] = copy.deepcopy(DEFAULTS[section][key])
        self.save()


def parse_value(raw: str, like: Any) -> Any:
    raw = raw.strip()
    if isinstance(like, bool):
        if raw.lower() in ("on", "true", "yes", "1"):
            return True
        if raw.lower() in ("off", "false", "no", "0"):
            return False
        raise ValueError("expected on/off")
    if isinstance(like, int):
        return int(raw)
    if isinstance(like, float):
        return float(raw)
    if isinstance(like, list) or like is None and raw.startswith("["):
        if raw.startswith("["):
            items = tomllib.loads(f"v = {raw}")["v"]
        else:
            items = [x.strip() for x in raw.split(",") if x.strip()]
        if isinstance(like, list) and all(isinstance(x, str) for x in like):
            items = [str(x) for x in items]  # lists of words (highlights, ignores...) stay words
        return items
    return raw
