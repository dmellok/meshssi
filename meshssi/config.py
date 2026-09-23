"""User settings in ~/.config/meshssi/config.toml (created with defaults on first run)."""

import copy
import json
import os
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
        "timestamp_format": "%H:%M",
        "nicklist": True,
        "show_signal": True,  # hops / SNR tags after messages
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


def _toml_value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    raise TypeError(f"can't write {type(v).__name__} to TOML")


def dumps(data: dict[str, dict[str, Any]]) -> str:
    out = ["# meshssi settings. Edit here or with /set section.key value inside the app.\n"]
    for section, values in data.items():
        out.append(f"[{section}]")
        for k, v in values.items():
            key = k if k.replace("_", "").replace("-", "").isalnum() else json.dumps(k, ensure_ascii=False)
            out.append(f"{key} = {_toml_value(v)}")
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
            except tomllib.TOMLDecodeError as e:
                self.error = f"{path}: {e} (using defaults)"
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
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(dumps(self.data), encoding="utf-8")

    def __getitem__(self, section: str) -> dict[str, Any]:
        return self.data.setdefault(section, {})

    def get(self, dotted: str, default: Any = None) -> Any:
        section, _, key = dotted.partition(".")
        return self.data.get(section, {}).get(key, default)

    def set(self, dotted: str, raw: str) -> Any:
        """Set section.key from a string typed at the prompt, coercing to the existing type."""
        section, _, key = dotted.partition(".")
        if not key:
            raise KeyError("use section.key, e.g. chat.dm_retries")
        current = self.data.get(section, {}).get(key)
        if section not in self.data or (key not in self.data[section] and section not in ("rooms", "aliases", "radio_presets")):
            raise KeyError(f"unknown setting {dotted} — /set lists them")
        value = parse_value(raw, current)
        self.data[section][key] = value
        self.save()
        return value

    def unset(self, dotted: str) -> None:
        section, _, key = dotted.partition(".")
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
    if isinstance(like, list):
        if raw.startswith("["):
            return tomllib.loads(f"v = {raw}")["v"]
        return [x.strip() for x in raw.split(",") if x.strip()]
    return raw
