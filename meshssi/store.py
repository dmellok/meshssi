"""Scrollback and small state persisted under ~/.local/share/meshssi/<node>/."""

import hashlib
import json
import os
import re
from pathlib import Path

DATA_ROOT = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "meshssi"


def _safe(key: str) -> str:
    """A readable file name that's unique per window: '#vic' and '@vic', or two emoji-only names, must not
    share a log (and case-insensitive filesystems must not merge '#Vic' and '#vic')."""
    readable = re.sub(r"[^A-Za-z0-9_.-]", "_", key)[:40]
    return f"{readable}-{hashlib.sha256(key.encode()).hexdigest()[:10]}"


def _legacy(key: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", key)


class Store:
    def __init__(self, node_key: str, root: Path = DATA_ROOT):
        self.dir = root / node_key[:12]
        (self.dir / "logs").mkdir(parents=True, exist_ok=True, mode=0o700)
        for d in (root, self.dir, self.dir / "logs"):  # private messages live here
            try:
                os.chmod(d, 0o700)
            except OSError:
                pass

    def _log(self, win_key: str) -> Path:
        path = self.dir / "logs" / f"{_safe(win_key)}.jsonl"
        old = self.dir / "logs" / f"{_legacy(win_key)}.jsonl"
        if not path.exists() and old.exists():  # logs from before names got a unique suffix
            try:
                old.rename(path)
            except OSError:
                return old
        return path

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
        tmp = self.dir / "state.json.tmp"
        tmp.write_text(json.dumps(state, indent=2))
        os.replace(tmp, self.dir / "state.json")  # atomic: a reader never sees half a file
