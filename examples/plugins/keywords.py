"""Log any message mentioning watched keywords to a file, and flag it in the status window.

Copy to ~/.config/meshssi/plugins/. Settings (config.toml):

    [plugin.keywords]
    words = ["emergency", "help", "fire"]
    file = "~/meshssi-keywords.log"
"""

import time
from pathlib import Path


def setup(api):
    def check(where, rec):
        words = [w.lower() for w in api.config.get("words", ["emergency", "help"])]
        text = rec["text"].lower()
        hits = [w for w in words if w in text]
        if not hits:
            return
        api.echo(f"keyword {', '.join(hits)} from {rec['nick']} in {where}: {rec['text']}", "error")
        path = Path(api.config.get("file", "~/meshssi-keywords.log")).expanduser()
        with path.open("a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}\t{where}\t{rec['nick']}\t{rec['text']}\n")

    @api.on("channel_message")
    def channel(win, rec):
        check(win.name, rec)

    @api.on("dm")
    def dm(win, rec, contact):
        check("DM", rec)
