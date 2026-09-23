"""Scrollback and small state persisted under ~/.local/share/meshssi/<node>/."""

import json
import os
import re
from pathlib import Path

DATA_ROOT = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "meshssi"
CONFIG_PATH = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "meshssi" / "config.json"


def _safe(key: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", key)


class Store:
    def __init__(self, node_key: str):
        self.dir = DATA_ROOT / node_key[:12]
        (self.dir / "logs").mkdir(parents=True, exist_ok=True)

    def _log(self, win_key: str) -> Path:
        return self.dir / "logs" / f"{_safe(win_key)}.jsonl"

    def append(self, win_key: str, rec: dict) -> None:
        with self._log(win_key).open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def load(self, win_key: str, limit: int = 500) -> list[dict]:
        path = self._log(win_key)
        if not path.exists():
            return []
        recs: list[dict] = []
        acks: dict[str, str] = {}
        for line in path.read_text(encoding="utf-8").splitlines()[-limit * 2 :]:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("k") == "ack":
                acks[rec["ref"]] = rec["st"]
            else:
                recs.append(rec)
        for rec in recs:
            if rec.get("id") in acks:
                rec["st"] = acks[rec["id"]]
            elif rec.get("st") == "pending":
                rec["st"] = "fail"
        return recs[-limit:]

    def load_state(self) -> dict:
        try:
            return json.loads((self.dir / "state.json").read_text())
        except (OSError, json.JSONDecodeError):
            return {}

    def save_state(self, state: dict) -> None:
        (self.dir / "state.json").write_text(json.dumps(state, indent=2))


def load_config() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def save_config(cfg: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))
