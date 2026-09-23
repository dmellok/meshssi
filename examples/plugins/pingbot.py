"""Answer "!ping" with how the message reached us — handy for range testing.

Copy to ~/.config/meshssi/plugins/. Settings (config.toml):

    [plugin.pingbot]
    channels = ["#test"]   # channels to answer in; DMs are always answered
"""


def setup(api):
    def describe(rec):
        hops = rec.get("hops")
        route = "direct" if hops in (0, 255, None) else f"{hops} hop{'s' if hops > 1 else ''}"
        snr = f", {rec['snr']:+.1f} dB SNR" if rec.get("snr") is not None else ""
        return f"pong — heard you {route}{snr}"

    @api.on("dm")
    async def dm(win, rec, contact):
        if rec["text"].strip().lower() == "!ping":
            await api.reply(win, describe(rec))

    @api.on("channel_message")
    async def channel(win, rec):
        allowed = api.config.get("channels", ["#test"])
        if win.name in allowed and rec["text"].strip().lower() == "!ping":
            await api.reply(win, f"@[{rec['nick']}] {describe(rec)}")

    @api.command("pingbot", "Show which channels the ping bot answers in")
    def status(args):
        api.echo(f"answering !ping in DMs and {', '.join(api.config.get('channels', ['#test']))}")
