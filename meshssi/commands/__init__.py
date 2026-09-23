"""Slash command registry. Handlers are `async def fn(app, args: str)`, grouped by module."""

import time
from typing import Callable

from meshcore import EventType

COMMANDS: dict[str, tuple[Callable, str, str, str]] = {}  # name -> (fn, category, usage, help)
ALIASES: dict[str, str] = {}
CATEGORIES = ("chat", "contacts", "mesh", "remote", "device", "client", "plugins")
OFFLINE_OK = {"client", "plugins"}


def command(name: str, category: str, usage: str, help: str, aliases: tuple[str, ...] = ()):
    def deco(fn):
        COMMANDS[name] = (fn, category, usage, help)
        for a in aliases:
            ALIASES[a] = name
        return fn

    return deco


class CommandsMixin:
    def command_names(self) -> list[str]:
        return list(COMMANDS) + list(ALIASES) + list(self.cfg["aliases"])

    async def run_command(self, line: str, depth: int = 0) -> None:
        name, _, args = line.partition(" ")
        name = name.lower()
        user_alias = self.cfg["aliases"].get(name)
        if user_alias and depth < 5:
            body = user_alias.lstrip("/")
            expanded = body.replace("$*", args) if "$*" in body else body + (" " + args if args else "")
            await self.run_command(expanded, depth + 1)
            return
        name = ALIASES.get(name, name)
        if name not in COMMANDS:
            self.echo(f"Unknown command: /{name} — try /help", "error")
            return
        fn, category, _, _ = COMMANDS[name]
        if category not in OFFLINE_OK and not self.connected:
            self.echo("Not connected to a radio.", "error")
            return
        try:
            await fn(self, args.strip())
        except Exception as e:  # noqa: BLE001
            self.echo(f"/{name} failed: {type(e).__name__}: {e}", "error")

    def need_contact(self, query: str) -> dict | None:
        if not query:
            if self.win.kind == "query" and (c := self.contact(self.win.pubkey)):
                return c
            self.echo("Which contact? (tab completes names)", "error")
            return None
        c = self.find_contact(query)
        if not c:
            self.echo(f"No unique contact matches {query!r}. See /contacts.", "error")
        return c

    def need_channel(self, name: str):
        win = self.win if not name else next(
            (w for w in self.windows if w.kind == "channel" and w.name.lower() == name.lower()), None)
        if not win or win.kind != "channel":
            self.echo("Not a channel." if not name else f"Not in {name}.", "error")
            return None
        return win

    def confirm(self, action: str) -> bool:
        """Destructive commands must be issued twice within 10 seconds."""
        if self.pending_confirm and self.pending_confirm[0] == action and time.time() - self.pending_confirm[1] < 10:
            self.pending_confirm = None
            return True
        self.pending_confirm = (action, time.time())
        self.echo(f"Repeat the command within 10s to confirm: {action}", "error")
        return False

    async def cmd(self, coro):
        """Run one request/response command against the radio, raising on an error reply."""
        async with self.io:
            ev = await coro
        if ev is not None and ev.type == EventType.ERROR:
            raise RuntimeError(ev.payload.get("reason") or ev.payload.get("error_code") or ev.payload)
        return ev


# importing the modules registers their commands
from . import chat, client, contacts, device, mesh, remote  # noqa: E402,F401
