"""POST messages (and optionally adverts) as JSON to a URL — e.g. a Home Assistant webhook.

Copy to ~/.config/meshssi/plugins/. Settings (config.toml):

    [plugin.webhook]
    url = "http://homeassistant.local:8123/api/webhook/meshssi"
    events = ["dm", "channel_message"]   # add "advert" for every advert heard
"""

import asyncio
import json
import urllib.request


def setup(api):
    def post(payload: dict) -> None:
        url = api.config.get("url")
        if not url:
            return
        req = urllib.request.Request(url, data=json.dumps(payload, ensure_ascii=False).encode(),
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10).read()

    def wanted(event):
        return event in api.config.get("events", ["dm", "channel_message"])

    async def send(payload):
        try:
            await asyncio.to_thread(post, payload)
        except Exception as e:  # noqa: BLE001
            api.echo(f"webhook failed: {e}", "error")

    @api.on("dm")
    async def dm(win, rec, contact):
        if wanted("dm"):
            await send({"type": "dm", "from": rec["nick"], "text": rec["text"], "hops": rec.get("hops"), "snr": rec.get("snr")})

    @api.on("channel_message")
    async def channel(win, rec):
        if wanted("channel_message"):
            await send({"type": "channel", "channel": win.name, "from": rec["nick"], "text": rec["text"],
                        "hops": rec.get("hops"), "snr": rec.get("snr")})

    @api.on("advert")
    async def advert(node, packet):
        if wanted("advert"):
            await send({"type": "advert", "name": node.get("name"), "node_type": node.get("type"), "snr": packet.get("snr"),
                        "lat": node.get("lat"), "lon": node.get("lon")})
