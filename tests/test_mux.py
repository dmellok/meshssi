"""The daemon against a fake radio, with real TCP clients on the other side."""

import asyncio
import logging

from meshssi.mux import Mux

logging.getLogger("meshcore").setLevel(logging.CRITICAL)


class FakeRadio:
    """Speaks just enough of the companion protocol: one reply per request, contacts as a stream."""

    def __init__(self):
        self.reader = None
        self.inbox = [bytes([0x08, 0, 0, 0, 0, 0, 0]) + b"alice: hi", bytes([0x08, 0, 0, 0, 0, 0, 0]) + b"bob: yo"]

    def set_reader(self, r):
        self.reader = r

    def set_disconnect_callback(self, cb):
        pass

    async def connect(self):
        return "fake"

    async def send(self, frame: bytes):
        cmd = frame[0]
        await asyncio.sleep(0.01)
        if cmd == 0x01:  # app start -> self info
            await self.reader.handle_rx(bytes([0x05]) + b"\x01" * 60)
        elif cmd == 0x04:  # contacts: start, two contacts, end
            for code in (0x02, 0x03, 0x03, 0x04):
                await self.reader.handle_rx(bytes([code, 0]))
        elif cmd == 0x0A:
            await self.reader.handle_rx(self.inbox.pop(0) if self.inbox else bytes([0x0A]))
        elif cmd == 0x14:
            await self.reader.handle_rx(bytes([0x0C, 1, 2]))
        else:
            await self.reader.handle_rx(bytes([0x00]))


async def client(port: int, app: str):
    r, w = await asyncio.open_connection("127.0.0.1", port)

    async def req(payload: bytes, until=None, n=1):
        w.write(b"<" + len(payload).to_bytes(2, "little") + payload)
        await w.drain()
        frames = []
        while True:
            hdr = await asyncio.wait_for(r.readexactly(3), 5)
            body = await r.readexactly(int.from_bytes(hdr[1:], "little"))
            if body[0] >= 0x80:
                continue  # pushes
            frames.append(body)
            if (until is None and len(frames) >= n) or (until is not None and body[0] == until):
                return frames

    await req(bytes([0x01, 3]) + b"\0" * 6 + app.encode())
    return req, w


def test_mux_routes_replies_and_fans_out_messages():
    async def run():
        radio = FakeRadio()
        mux = Mux(lambda: radio, "127.0.0.1", 0)
        server = await asyncio.start_server(mux.on_client, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        tasks = [asyncio.create_task(mux.keep_upstream()), asyncio.create_task(mux.pump())]
        await mux.upstream_up.wait()
        await asyncio.sleep(0.3)  # daemon fetches the two waiting messages into its log

        a, wa = await client(port, "meshssi")
        b, wb = await client(port, "meshssi")  # same app name, connected at the same time

        async def hammer(req):
            for _ in range(20):
                assert (await req(bytes([0x14])))[0][0] == 0x0C  # battery reply always goes to the asker
                contacts = await req(bytes([0x04]), until=0x04)
                assert [f[0] for f in contacts] == [0x02, 0x03, 0x03, 0x04]

        await asyncio.gather(hammer(a), hammer(b))
        for req in (a, b):  # both see both messages, then "no more"
            texts = [(await req(bytes([0x0A])))[0] for _ in range(3)]
            assert texts[0].endswith(b"alice: hi") and texts[1].endswith(b"bob: yo") and texts[2] == bytes([0x0A])
        wa.close()
        wb.close()
        for t in tasks:
            t.cancel()
        server.close()

    asyncio.run(run())
