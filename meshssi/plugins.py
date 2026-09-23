"""Python plugins, loaded from ~/.config/meshssi/plugins/*.py.

A plugin module defines `setup(api)`. See examples/plugins/ for working ones.

    def setup(api):
        @api.on("dm")
        async def ping(win, rec, contact):
            if rec["text"].strip().lower() == "!ping":
                await api.reply(win, "pong")

        @api.command("hello", "Say hello in the current window")
        async def hello(args):
            await api.say(api.current_window, f"hello {args}")

Events: connect, channel_message(win, rec), dm(win, rec, contact), sent(win, rec),
packet(packet), advert(node, packet).
"""

import asyncio
import importlib.util
import inspect
import traceback
from collections import defaultdict

from .config import PLUGIN_DIR

EVENTS = {"connect", "channel_message", "dm", "sent", "packet", "advert"}


class PluginAPI:
    def __init__(self, app, name: str):
        self.app = app
        self.name = name

    # registration
    def on(self, event: str):
        if event not in EVENTS:
            raise ValueError(f"unknown event {event!r}; one of {sorted(EVENTS)}")

        def deco(fn):
            self.app.plugins.handlers[event].append((self.name, fn))
            return fn

        return deco

    def command(self, name: str, help: str = "", usage: str | None = None):
        from .commands import COMMANDS

        def deco(fn):
            async def run(app, args):
                result = fn(args)
                if inspect.isawaitable(result):
                    await result

            COMMANDS[name] = (run, "plugins", usage or f"/{name}", f"{help} [{self.name}]")
            return fn

        return deco

    # actions
    @property
    def current_window(self):
        return self.app.win

    @property
    def windows(self):
        return self.app.windows

    @property
    def contacts(self) -> dict:
        return self.app.mc.contacts if self.app.mc else {}

    @property
    def me(self) -> str:
        return self.app.my_name

    @property
    def config(self) -> dict:
        """This plugin's own settings: the [plugin.<name>] section of config.toml."""
        return self.app.cfg[f"plugin.{self.name}"]

    def window(self, name: str):
        return next((w for w in self.app.windows if w.name.lower() == name.lower()), None)

    async def say(self, target, text: str) -> None:
        """Send to a window object, a channel name ("#vic"), or a contact name."""
        win = target if hasattr(target, "kind") else self.window(target)
        if win is None and (c := self.app.find_contact(str(target))):
            win = self.app.query_window(c)
        if win is None:
            raise ValueError(f"no window or contact called {target!r}")
        await self.app.say(win, text)

    reply = say

    def echo(self, text: str, level: str = "info") -> None:
        self.app.status(f"[{self.name}] {text}", level)

    async def radio(self, coro_fn, *args, **kw):
        """Run a meshcore command (e.g. api.app.mc.commands.get_bat) with the radio lock held."""
        async with self.app.io:
            return await coro_fn(*args, **kw)


class PluginManager:
    def __init__(self, app):
        self.app = app
        self.handlers: dict[str, list] = defaultdict(list)
        self.loaded: list[str] = []

    def load_all(self) -> list[tuple[str, str]]:
        msgs = []
        if not PLUGIN_DIR.is_dir():
            return msgs
        for path in sorted(PLUGIN_DIR.glob("*.py")):
            name = path.stem
            try:
                spec = importlib.util.spec_from_file_location(f"meshssi_plugin_{name}", path)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                if hasattr(mod, "setup"):
                    mod.setup(PluginAPI(self.app, name))
                self.loaded.append(name)
            except Exception as e:  # noqa: BLE001
                msgs.append((f"Plugin {name} failed to load: {type(e).__name__}: {e}", "error"))
        if self.loaded:
            msgs.append((f"Plugins loaded: {', '.join(self.loaded)}", "ok"))
        return msgs

    def emit(self, event: str, **kw) -> None:
        for name, fn in self.handlers.get(event, []):
            asyncio.get_running_loop().create_task(self._call(name, fn, kw))

    async def _call(self, name, fn, kw) -> None:
        try:
            result = fn(**kw)
            if inspect.isawaitable(result):
                await result
        except Exception as e:  # noqa: BLE001
            self.app.status(f"Plugin {name} error in handler {fn.__name__}: {type(e).__name__}: {e}", "error")
            self.app.log(traceback.format_exc())
