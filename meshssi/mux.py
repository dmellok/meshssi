"""Companion multiplexer: one radio connection, many clients.

`meshssi --serve <target>` holds the single connection a MeshCore companion allows and serves the same
framed protocol on a local TCP port, so several clients (meshssi instances, Home Assistant, scripts) can
share one radio. Requests are serialised; replies go back to whoever asked; push notifications go to
everyone. Incoming messages are fetched by the daemon and fanned out, so each client (keyed by the app
name it sends in APP_START) reads its own copy from a shared log — including messages that arrived while
it was away. Two clients with the same app name connected at once each still see every message.
"""

import asyncio
import collections
import logging
import time

log = logging.getLogger("meshssi.mux")

CMD_APP_START = 0x01
CMD_GET_CONTACTS = 0x04
CMD_SYNC_NEXT_MESSAGE = 0x0A
RESP_ERROR = 0x01
RESP_CONTACT_END = 0x04
RESP_NO_MORE_MSGS = 0x0A
PUSH_MSG_WAITING = 0x83
MESSAGE_CODES = {0x07, 0x08, 0x10, 0x11, 0x1B}  # contact/channel msg (v2, v3), channel data
QUEUE_LIMIT = 1000
REPLY_TIMEOUT = 10.0


def frame_out(data: bytes) -> bytes:
    """Radio→client framing."""
    return b">" + len(data).to_bytes(2, "little") + data


class Client:
    def __init__(self, mux: "Mux", reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self.mux, self.reader, self.writer = mux, reader, writer
        self.app = "unknown"
        self.cursor = 0  # sequence number of the last message this connection has read
        peer = writer.get_extra_info("peername")
        self.peer = f"{peer[0]}:{peer[1]}" if peer else "?"

    def send(self, data: bytes) -> None:
        if not self.writer.is_closing():
            self.writer.write(frame_out(data))

    async def run(self) -> None:
        buf = b""
        try:
            while chunk := await self.reader.read(4096):
                buf += chunk
                while True:
                    start = buf.find(b"<")
                    if start < 0:
                        buf = b""
                        break
                    buf = buf[start:]
                    if len(buf) < 3:
                        break
                    size = int.from_bytes(buf[1:3], "little")
                    if len(buf) < 3 + size:
                        break
                    frame, buf = buf[3 : 3 + size], buf[3 + size :]
                    if frame:
                        await self.mux.from_client(self, frame)
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            self.writer.close()


class Mux:
    def __init__(self, make_transport, listen_host: str, listen_port: int, fetch_messages: bool = True):
        self.make_transport = make_transport
        self.listen = (listen_host, listen_port)
        self.fetch_messages = fetch_messages
        self.transport = None
        self.clients: set[Client] = set()
        self.messages: collections.deque = collections.deque(maxlen=QUEUE_LIMIT)  # (seq, frame)
        self.seq = 0
        self.app_cursor: dict[str, int] = {}  # app name -> last seq read, so reconnects resume
        self.requests: asyncio.Queue = asyncio.Queue()  # (client | None, frame, future | None)
        self.current: tuple[Client | None, int] | None = None  # who gets the next reply, and their command
        self.reply_done: asyncio.Event = asyncio.Event()
        self.upstream_up = asyncio.Event()
        self.fetching = False
        self.last_code: int | None = None
        self.stats = collections.Counter()

    # ── upstream (radio) ──────────────────────────────────────────────────
    async def handle_rx(self, frame: bytes) -> None:  # called by the meshcore transport per frame
        if not frame:
            return
        code = frame[0]
        self.stats["rx_frames"] += 1
        if code >= 0x80:
            if code == PUSH_MSG_WAITING and self.fetch_messages:
                self.start_fetch()
                return
            for c in list(self.clients):
                c.send(frame)
            return
        if self.current is None:
            log.debug("unsolicited reply 0x%02x dropped", code)
            return
        client, cmd = self.current
        self.last_code = code
        if client is None:  # the daemon's own requests (app start, message fetch)
            if code in MESSAGE_CODES:
                self.fan_out(frame)
            self.reply_done.set()
            return
        client.send(frame)
        if cmd == CMD_GET_CONTACTS and code not in (RESP_CONTACT_END, RESP_ERROR):
            return  # contacts stream as START, CONTACT…, END
        self.reply_done.set()

    async def on_upstream_lost(self, reason: str) -> None:
        log.warning("radio connection lost (%s)", reason)
        self.upstream_up.clear()
        self.reply_done.set()

    async def keep_upstream(self) -> None:
        delay = 2
        while True:
            if not self.upstream_up.is_set():
                try:
                    self.transport = self.make_transport()
                    self.transport.set_reader(self)
                    self.transport.set_disconnect_callback(self.on_upstream_lost)
                    await self.transport.connect()
                    self.upstream_up.set()
                    delay = 2
                    log.info("connected to radio")
                    await self.requests.put((None, bytes([CMD_APP_START, 3]) + b"\0" * 6 + b"meshssi-mux", None))
                    self.start_fetch()
                except Exception as e:  # noqa: BLE001
                    log.warning("radio connect failed: %s; retrying in %ss", e, delay)
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 60)
                    continue
            await asyncio.sleep(1)

    async def pump(self) -> None:
        """Send queued requests to the radio one at a time."""
        while True:
            client, frame, fut = await self.requests.get()
            if client is not None and client not in self.clients:
                continue
            await self.upstream_up.wait()
            self.current = (client, frame[0])
            self.last_code = None
            self.reply_done.clear()
            try:
                await self.transport.send(frame)
                await asyncio.wait_for(self.reply_done.wait(), REPLY_TIMEOUT)
            except asyncio.TimeoutError:
                log.debug("no reply to 0x%02x", frame[0])
            except Exception as e:  # noqa: BLE001
                log.warning("send failed: %s", e)
            finally:
                self.current = None
                if fut and not fut.done():
                    fut.set_result(self.last_code)

    # ── message fan-out ───────────────────────────────────────────────────
    def start_fetch(self) -> None:
        if not self.fetching:
            self.fetching = True
            asyncio.create_task(self._fetch_loop())

    async def _fetch_loop(self) -> None:
        try:
            while True:
                fut = asyncio.get_running_loop().create_future()
                await self.requests.put((None, bytes([CMD_SYNC_NEXT_MESSAGE]), fut))
                if await fut not in MESSAGE_CODES:
                    break
        finally:
            self.fetching = False

    def fan_out(self, frame: bytes) -> None:
        self.stats["messages"] += 1
        self.seq += 1
        self.messages.append((self.seq, frame))
        for c in list(self.clients):
            c.send(bytes([PUSH_MSG_WAITING]))

    def pending(self, client: Client) -> int:
        return sum(1 for seq, _ in self.messages if seq > client.cursor)

    def next_message(self, client: Client) -> bytes | None:
        for seq, frame in self.messages:
            if seq > client.cursor:
                client.cursor = seq
                self.app_cursor[client.app] = max(self.app_cursor.get(client.app, 0), seq)
                return frame
        return None

    # ── clients ───────────────────────────────────────────────────────────
    async def from_client(self, client: Client, frame: bytes) -> None:
        cmd = frame[0]
        if cmd == CMD_APP_START:
            client.app = frame[8:].decode("utf-8", "ignore").strip("\0") or "unknown"
            # a returning app resumes where it left off; a new one gets everything still buffered
            client.cursor = self.app_cursor.get(client.app, 0)
            log.info("%s identified as %r (%d unread messages)", client.peer, client.app, self.pending(client))
        if cmd == CMD_SYNC_NEXT_MESSAGE:
            client.send(self.next_message(client) or bytes([RESP_NO_MORE_MSGS]))
            return
        await self.requests.put((client, frame, None))
        if cmd == CMD_APP_START and self.pending(client):
            client.send(bytes([PUSH_MSG_WAITING]))

    async def on_client(self, reader, writer) -> None:
        client = Client(self, reader, writer)
        self.clients.add(client)
        log.info("client connected from %s (%d total)", client.peer, len(self.clients))
        try:
            await client.run()
        finally:
            self.clients.discard(client)
            log.info("client %s (%s) left", client.peer, client.app)

    async def serve(self) -> None:
        try:
            server = await asyncio.start_server(self.on_client, *self.listen)
        except OSError as e:
            raise SystemExit(f"meshssi: can't listen on {self.listen[0]}:{self.listen[1]} ({e.strerror}). "
                             "Is another meshssi --serve already running? Pick another with --listen.")
        log.info("serving MeshCore companion protocol on %s:%s", *self.listen)
        async with server:
            await asyncio.gather(self.keep_upstream(), self.pump(), server.serve_forever())


def make_transport_factory(target: str, baud: int = 115200, pin: str = ""):
    from meshcore.ble_cx import BLEConnection
    from meshcore.serial_cx import SerialConnection
    from meshcore.tcp_cx import TCPConnection

    if target.startswith("ble"):
        address = target.partition(":")[2] or None
        return lambda: BLEConnection(address=address, pin=pin or None)
    if target.startswith("/dev/") or target.upper().startswith("COM"):
        return lambda: SerialConnection(target, baud)
    host, _, port = target.partition(":")
    return lambda: TCPConnection(host, int(port or 5000))


def run_daemon(target: str, listen: str, baud: int = 115200, pin: str = "") -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("meshcore").setLevel(logging.WARNING)
    host, _, port = listen.rpartition(":")
    mux = Mux(make_transport_factory(target, baud, pin), host or "127.0.0.1", int(port))
    started = time.time()
    try:
        asyncio.run(mux.serve())
    except KeyboardInterrupt:
        log.info("stopped after %.0fs: %s", time.time() - started, dict(mux.stats))
