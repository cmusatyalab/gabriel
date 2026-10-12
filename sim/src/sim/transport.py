"""Point-to-point transports between a client and a server.

Every transport follows the edge rules in CLAUDE.md:

- Each data channel (peer, channel, direction) is a **single slot**: a newer
  payload overwrites an older one that has not been consumed yet. Nothing
  queues. Channels are named freely; the flow layer uses one channel per
  graph edge (``flow|parent|child``), so every flow and every edge of a flow
  is independent of the others.
- Control messages (demands, pings, flow setup) travel on the `CONTROL`
  channel, which is a reliable FIFO kept separate from data, so control is
  never stuck behind data and is never overwritten.
- Nothing is replayed after an outage: a new connection starts with empty
  slots.

Two transports exist: `IceoryxTransport` for peers that share a host (shared
memory) and `QuicTransport` for remote hosts. Because the QUIC transport uses
real UDP sockets, it runs unchanged inside network namespaces (e.g. ones set
up with pyroute2 and shaped with netem); create the transport after entering
the namespace so its sockets are bound there.
"""

import asyncio
import ctypes
import ssl
import struct
import uuid
from collections import deque
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from functools import partial

import iceoryx2 as iox
from aioquic.asyncio import connect, serve
from aioquic.asyncio.protocol import QuicConnectionProtocol
from aioquic.quic.configuration import QuicConfiguration
from aioquic.quic.events import (
    ConnectionTerminated,
    StreamDataReceived,
    StreamReset,
)

# Reliable FIFO channel for control messages.
CONTROL = "control"
# Default data channel. Any other name is also a data channel.
DATA = "data"

# Called for every data payload a QUIC transport receives with
# (client_id or None, channel, payload bytes, seconds from first to last byte).
FrameHook = Callable[[str | None, str, int, float], None]
# Called when a newer payload replaces one still waiting to be sent, with
# (client_id or None, channel, the replaced payload).
ReplaceHook = Callable[[str | None, str, bytes], None]

iox.set_log_level_from_env_or(iox.LogLevel.Warn)


def _check_channel(channel: str):
    if not isinstance(channel, str) or not channel:
        raise ValueError(f"invalid channel {channel!r}")


class _Slot:
    """Single-value mailbox. A put overwrites any value not yet taken."""

    def __init__(self):
        self._value = None
        self._ready = asyncio.Event()
        self._closed = False

    def put(self, value: bytes) -> bool:
        """Store a value.

        Returns:
            bool: True if an untaken value was overwritten
        """
        overwrote = self._value is not None
        self._value = value
        self._ready.set()
        return overwrote

    async def take(self) -> bytes:
        """Wait for a value and remove it from the slot.

        Raises:
            ConnectionError: the slot was closed while empty
        """
        while self._value is None:
            if self._closed:
                raise ConnectionError("connection closed")
            self._ready.clear()
            await self._ready.wait()
        value, self._value = self._value, None
        return value

    def close(self):
        """Wake every waiter; later takes fail once the slot is empty."""
        self._closed = True
        self._ready.set()


class _Inbox:
    """Everything received from one peer: one slot per data channel and a
    FIFO for control. Takers can wait on one channel or on any of them."""

    def __init__(self):
        self.slots: dict[str, bytes] = {}  # full data slots, in fill order
        self.control: deque[bytes] = deque()
        self._ready = asyncio.Event()
        self.closed = False

    def put(self, channel: str, payload: bytes) -> bool:
        """Store a payload.

        Returns:
            bool: True if an untaken data payload was overwritten
        """
        if self.closed:
            return False
        overwrote = False
        if channel == CONTROL:
            self.control.append(payload)
        else:
            overwrote = channel in self.slots
            self.slots[channel] = payload
        self._ready.set()
        return overwrote

    def _pop(self, channel: str | None) -> tuple[str, bytes] | None:
        if channel == CONTROL or (channel is None and self.control):
            if self.control:
                return CONTROL, self.control.popleft()
            return None
        if channel is None:
            if self.slots:
                channel = next(iter(self.slots))
            else:
                return None
        payload = self.slots.pop(channel, None)
        return None if payload is None else (channel, payload)

    async def take(self, channel: str | None = None) -> tuple[str, bytes]:
        """Wait for a payload on `channel` (any channel if None; control
        first) and remove it.

        Raises:
            ConnectionError: the inbox was closed and nothing is left
        """
        while (item := self._pop(channel)) is None:
            if self.closed:
                raise ConnectionError("connection closed")
            self._ready.clear()
            await self._ready.wait()
        return item

    def close(self):
        self.closed = True
        self._ready.set()


class Transport:
    """Transport is the wrapper class for a connection.

    A transport is used either as a client (`connect`, `send_cli`, `recv_cli`)
    or as a server (`bind`, `accept`, `send_srv`, `recv_srv`), not both.

    Attributes:
        client_id (str): identifier this transport presents when connecting
        send_overwrites (int): payloads replaced before they were sent
        recv_overwrites (int): payloads replaced before they were received
        on_frame (FrameHook | None): called for every received data payload
    """

    def __init__(self, client_id: str | None = None):
        self.client_id = client_id or uuid.uuid4().hex
        self.send_overwrites = 0
        self.recv_overwrites = 0
        self.on_frame: FrameHook | None = None
        self.on_replace: ReplaceHook | None = None

    async def connect(self, endpoint: str) -> bool:
        """Connect to a target endpoint.

        Args:
            endpoint (str): target endpoint URI

        Returns:
            bool: True if the connect suceeds, False otherwise
        """
        return False

    async def is_connected(self, client_id: str | None = None) -> bool:
        """Check if the connection is active.

        Args:
            client_id (str | None): on a server, the client to check; if
                omitted, whether the server is still bound

        Returns:
            bool: True if the connection is active, False otherwise
        """
        return False

    async def bind(self, endpoint: str) -> bool:
        """Bind to a target endpoint.

        Args:
            endpoint (str): target endpoint URI

        Returns:
            bool: True if the bind suceeds, False otherwise
        """
        return False

    async def accept(self) -> str:
        """Wait for a new client to connect (server only).

        Returns:
            str: identifier of the new client
        """
        raise NotImplementedError

    async def close(self):
        """Close the connection (client) or every connection (server)."""

    async def send_cli(self, payload: bytes, channel: str = DATA) -> bool:
        """Send data over the connection (client only).

        On a data channel, overwrites any payload on the same channel that
        has not been sent yet. On `CONTROL`, the payload is queued.

        Args:
            payload (bytes): data payload
            channel (str): channel to send on

        Returns:
            bool: True if the send succeeds, False otherwise
        """
        return False

    def send_cli_nowait(self, payload: bytes, channel: str = DATA) -> bool:
        """`send_cli` for callers that are not coroutines (sends never block)."""
        return False

    def send_srv_nowait(self, payload: bytes, client_id: str, channel: str = DATA) -> bool:
        """`send_srv` for callers that are not coroutines (sends never block)."""
        return False

    async def recv_cli(self, channel: str = DATA) -> bytes:
        """Receive data over the connection (client only).

        Waits for the newest payload on a data channel, or the oldest
        unread message on `CONTROL`.

        Args:
            channel (str): channel to receive from

        Returns:
            bytes: bytes received

        Raises:
            ConnectionError: the connection was lost
        """
        raise NotImplementedError

    async def recv_any_cli(self) -> tuple[str, bytes]:
        """Receive from whichever channel has something (client only).
        Control is returned before data.

        Returns:
            tuple[str, bytes]: channel and payload

        Raises:
            ConnectionError: the connection was lost
        """
        raise NotImplementedError

    async def send_srv(self, payload: bytes, client_id: str, channel: str = DATA) -> bool:
        """Send data over the connection to a client (server only).

        Same channel semantics as `send_cli`.

        Args:
            payload (bytes): data payload
            client_id (str): client identifier
            channel (str): channel to send on

        Returns:
            bool: True if the send succeeds, False otherwise
        """
        return False

    async def recv_srv(self, client_id: str, channel: str = DATA) -> bytes:
        """Receive data over the connection (server only).

        Same channel semantics as `recv_cli`.

        Args:
            client_id (str): client identifier
            channel (str): channel to receive from

        Returns:
            bytes: bytes received

        Raises:
            ConnectionError: the client is not connected or was lost
        """
        raise NotImplementedError

    async def recv_any_srv(self, client_id: str) -> tuple[str, bytes]:
        """Receive from whichever channel of a client has something (server
        only). Control is returned before data.

        Raises:
            ConnectionError: the client is not connected or was lost
        """
        raise NotImplementedError


# ---------------------------------------------------------------------------
# iceoryx2
# ---------------------------------------------------------------------------

# Readers block in iceoryx2 listeners on these threads (the GIL is released
# while waiting). Threads are only spawned as needed.
_LISTENER_POOL = ThreadPoolExecutor(max_workers=256, thread_name_prefix="iox-listener")
# Initial shared-memory slice size; it grows in powers of two on demand.
_INITIAL_SLICE_LEN = 4096
# Number of hellos the server can hold between reads.
_HELLO_BUFFER = 64
# Clients that can be connecting to one server at the same time.
_HELLO_WRITERS = 128
# Samples a reader can hold between drains. Every channel of a peer shares one
# shared-memory service per direction (the reader drains it continuously and
# keeps the newest per channel), so this only has to cover drain latency.
_DATA_BUFFER = 16
_CONTROL_BUFFER = 256
# Every shared-memory payload is prefixed with the writer's sequence number,
# so a reader can count the values that were lost before it read them.
_SEQ = struct.Struct("<Q")
_CHANNEL_LEN = struct.Struct("<H")


def _frame_channel(channel: str, payload: bytes) -> bytes:
    name = channel.encode()
    return _CHANNEL_LEN.pack(len(name)) + name + payload


def _unframe_channel(frame: bytes, offset: int = 0) -> tuple[str, bytes]:
    (length,) = _CHANNEL_LEN.unpack_from(frame, offset)
    start = offset + _CHANNEL_LEN.size
    return frame[start:start + length].decode(), frame[start + length:]


class _ShmPort:
    """One end of a one-way shared-memory service carrying payloads, plus an
    optional event service that wakes the reader.

    Shared memory overwrites unread values silently (safe overflow), so each
    payload carries a sequence number and the reader counts the gaps.
    """

    def __init__(
        self,
        node,
        name: str,
        writer: bool,
        buffer: int = 1,
        create: bool = True,
        events: str | None = None,
        writers: int = 1,
    ):
        builder = (
            node.service_builder(iox.ServiceName.new(name))
            .publish_subscribe(iox.Slice[ctypes.c_uint8])
            .subscriber_max_buffer_size(buffer)
            .history_size(0)
            .enable_safe_overflow(True)
            .max_publishers(writers)
            .max_nodes(writers + 1)
        )
        self.service = builder.open_or_create() if create else builder.open()
        event_service = None
        if events is not None:
            event_service = (
                node.service_builder(iox.ServiceName.new(events)).event()
                .max_notifiers(writers)
                .max_nodes(writers + 1)
            )
            event_service = event_service.open_or_create() if create else event_service.open()
        self._notifier = None
        self._listener = None
        if writer:
            self._publisher = (
                self.service.publisher_builder()
                .initial_max_slice_len(_INITIAL_SLICE_LEN)
                .allocation_strategy(iox.AllocationStrategy.PowerOfTwo)
                .create()
            )
            if event_service is not None:
                self._notifier = event_service.notifier_builder().create()
            self._next_seq = 0
        else:
            self._subscriber = self.service.subscriber_builder().buffer_size(buffer).create()
            if event_service is not None:
                self._listener = event_service.listener_builder().create()
            self._wait = None
            # Readers are always created before their writer first writes
            # (connect waits for them), so the first payload has sequence 0.
            self._last_seq = -1
            # Payloads lost before they were read
            self.overwrites = 0

    @property
    def readers(self) -> int:
        self.service.try_cleanup_dead_nodes()
        return self.service.dynamic_config.number_of_subscribers

    @property
    def writers(self) -> int:
        self.service.try_cleanup_dead_nodes()
        return self.service.dynamic_config.number_of_publishers

    def write(self, payload: bytes, notify: bool = True):
        frame = _SEQ.pack(self._next_seq) + payload
        self._next_seq += 1
        sample = self._publisher.loan_slice_uninit(len(frame))
        ctypes.memmove(sample.payload_ptr, frame, len(frame))
        sample.assume_init().send()
        if notify and self._notifier is not None:
            self._notifier.notify()

    def notify(self):
        self._notifier.notify()

    def drain(self) -> list[bytes]:
        """Take every payload currently buffered, oldest first."""
        payloads = []
        while (sample := self._subscriber.receive()) is not None:
            frame = bytes(sample.payload().as_memory_view())
            sample.delete()
            (seq,) = _SEQ.unpack_from(frame)
            # A sequence that goes backwards means the writer was recreated
            # (the peer reconnected); there is no gap to count across that.
            if seq > self._last_seq:
                self.overwrites += seq - self._last_seq - 1
            self._last_seq = seq
            payloads.append(frame[_SEQ.size:])
        return payloads

    async def wait(self, timeout: float):
        """Wait until the writer signals or the timeout passes."""
        # A cancelled wait keeps running on its thread; reuse it rather than
        # using the listener from two threads at once.
        if self._wait is None or self._wait.done():
            loop = asyncio.get_running_loop()
            self._wait = loop.run_in_executor(
                _LISTENER_POOL,
                self._listener.timed_wait,
                iox.Duration.from_secs_f64(timeout),
            )
        await asyncio.shield(self._wait)


class _ShmPeer:
    """The ports one side holds for a single client connection: a data and a
    control service per direction, and one wake-up event per direction."""

    def __init__(self, node, endpoint: str, client_id: str, is_client: bool):
        base = f"{endpoint}/client/{client_id}"
        out_dir, in_dir = ("c2s", "s2c") if is_client else ("s2c", "c2s")
        self.out_data = _ShmPort(
            node, f"{base}/data/{out_dir}", writer=True, buffer=_DATA_BUFFER,
            events=f"{base}/ready/{out_dir}",
        )
        self.out_control = _ShmPort(
            node, f"{base}/control/{out_dir}", writer=True, buffer=_CONTROL_BUFFER
        )
        self.in_data = _ShmPort(
            node, f"{base}/data/{in_dir}", writer=False, buffer=_DATA_BUFFER,
            events=f"{base}/ready/{in_dir}",
        )
        self.in_control = _ShmPort(
            node, f"{base}/control/{in_dir}", writer=False, buffer=_CONTROL_BUFFER
        )
        self.inbox = _Inbox()
        self.reader: asyncio.Task | None = None

    def alive(self) -> bool:
        """Whether the other side still reads what this side writes."""
        if self.out_data is None:
            return False
        return self.out_data.readers > 0 and self.out_control.readers > 0

    def write(self, channel: str, payload: bytes):
        if channel == CONTROL:
            self.out_control.write(payload)
            self.out_data.notify()  # the data port's event wakes the reader
        else:
            self.out_data.write(_frame_channel(channel, payload))

    def release(self):
        """Drop the shared-memory ports so the other side sees this side
        leave (the reader task and this peer reference each other, so
        waiting for garbage collection is not enough)."""
        self.out_data = self.out_control = self.in_data = self.in_control = None

    async def read_forever(self, transport: "IceoryxTransport"):
        """Move everything the peer writes into the inbox until it leaves."""
        try:
            while True:
                lost = self.in_data.overwrites
                for payload in self.in_control.drain():
                    self.inbox.put(CONTROL, payload)
                for frame in self.in_data.drain():
                    channel, payload = _unframe_channel(frame)
                    if self.inbox.put(channel, payload):
                        transport.recv_overwrites += 1
                # Samples lost in shared memory before they were drained
                transport.recv_overwrites += self.in_data.overwrites - lost
                if not self.alive():
                    break
                await self.in_data.wait(IceoryxTransport.POLL_INTERVAL)
        finally:
            self.inbox.close()
            transport._on_peer_gone(self)
            self.release()


class IceoryxTransport(Transport):
    """Connects over Iceoryx2 shared memory.

    The endpoint is a service-name prefix (e.g. ``gabriel/edge0``). The server
    listens for hellos on ``<endpoint>/hello``; each client then gets one
    data and one control service per direction under
    ``<endpoint>/client/<client_id>/``. Data channels share the data service;
    the reader drains it continuously and keeps the newest payload per
    channel. Writes go straight to shared memory, so `send_overwrites` is
    always 0; values replaced before the application took them are counted in
    `recv_overwrites`.
    """

    # Seconds between liveness checks while a reader waits.
    POLL_INTERVAL = 0.05
    # Seconds a client waits for the server to pick up its hello.
    CONNECT_TIMEOUT = 2.0

    def __init__(self, client_id: str | None = None):
        super().__init__(client_id)
        self._node = iox.NodeBuilder.new().create(iox.ServiceType.Ipc)
        # Client state
        self._server: _ShmPeer | None = None
        # Server state
        self._hello = None
        self._clients: dict[str, _ShmPeer] = {}
        self._new_clients: asyncio.Queue[str] = asyncio.Queue()
        self._hello_task = None

    async def connect(self, endpoint: str) -> bool:
        try:
            hello = _ShmPort(
                self._node, f"{endpoint}/hello", writer=True, buffer=_HELLO_BUFFER,
                create=False, events=f"{endpoint}/hello/ready", writers=_HELLO_WRITERS,
            )
        except (iox.PublishSubscribeOpenError, iox.EventOpenError):
            return False  # no server bound
        peer = _ShmPeer(self._node, endpoint, self.client_id, is_client=True)

        # Repeat the hello until the server has subscribed to every channel,
        # since a hello sent while the server is still starting can be lost.
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.CONNECT_TIMEOUT
        while not peer.alive():
            if loop.time() >= deadline:
                return False
            hello.write(self.client_id.encode())
            await asyncio.sleep(0.005)
        self._server = peer
        peer.reader = asyncio.create_task(peer.read_forever(self))
        return True

    def _on_peer_gone(self, peer: _ShmPeer):
        for client_id, known in list(self._clients.items()):
            if known is peer:
                del self._clients[client_id]

    async def is_connected(self, client_id: str | None = None) -> bool:
        if self._hello is not None:
            if client_id is None:
                return True
            peer = self._clients.get(client_id)
            return peer is not None and not peer.inbox.closed and peer.alive()
        peer = self._server
        return peer is not None and not peer.inbox.closed and peer.alive()

    async def bind(self, endpoint: str) -> bool:
        try:
            self._hello = _ShmPort(
                self._node, f"{endpoint}/hello", writer=False, buffer=_HELLO_BUFFER,
                events=f"{endpoint}/hello/ready", writers=_HELLO_WRITERS,
            )
        except (iox.PublishSubscribeOpenOrCreateError, iox.EventOpenOrCreateError):
            return False
        self._hello_task = asyncio.create_task(self._accept_hellos(endpoint))
        return True

    async def _accept_hellos(self, endpoint: str):
        while True:
            for raw in self._hello.drain():
                client_id = raw.decode()
                peer = self._clients.get(client_id)
                if peer is not None and not peer.inbox.closed and peer.alive():
                    continue  # repeated hello from a live client
                if peer is not None and peer.reader is not None:
                    peer.reader.cancel()  # its cleanup releases the ports
                peer = _ShmPeer(self._node, endpoint, client_id, is_client=False)
                self._clients[client_id] = peer
                peer.reader = asyncio.create_task(peer.read_forever(self))
                self._new_clients.put_nowait(client_id)
            await self._hello.wait(self.POLL_INTERVAL)

    async def accept(self) -> str:
        return await self._new_clients.get()

    async def close(self):
        if self._hello_task is not None:
            self._hello_task.cancel()
            self._hello_task = None
        peers = list(self._clients.values())
        if self._server is not None:
            peers.append(self._server)
        for peer in peers:
            if peer.reader is not None:
                peer.reader.cancel()
                await asyncio.gather(peer.reader, return_exceptions=True)
                peer.reader = None
            peer.inbox.close()
            peer.release()
        self._hello = None
        self._clients.clear()
        self._server = None

    def _send(self, peer: _ShmPeer | None, payload: bytes, channel: str) -> bool:
        _check_channel(channel)
        if peer is None or peer.inbox.closed or peer.out_data is None:
            return False
        peer.write(channel, payload)
        return True

    def send_cli_nowait(self, payload: bytes, channel: str = DATA) -> bool:
        return self._send(self._server, payload, channel)

    async def send_cli(self, payload: bytes, channel: str = DATA) -> bool:
        return self.send_cli_nowait(payload, channel)

    async def recv_cli(self, channel: str = DATA) -> bytes:
        _check_channel(channel)
        if self._server is None:
            raise ConnectionError("not connected")
        return (await self._server.inbox.take(channel))[1]

    async def recv_any_cli(self) -> tuple[str, bytes]:
        if self._server is None:
            raise ConnectionError("not connected")
        return await self._server.inbox.take()

    def _peer(self, client_id: str) -> _ShmPeer:
        peer = self._clients.get(client_id)
        if peer is None:
            raise ConnectionError(f"client {client_id!r} is not connected")
        return peer

    def send_srv_nowait(self, payload: bytes, client_id: str, channel: str = DATA) -> bool:
        peer = self._clients.get(client_id)
        if peer is None or not peer.alive():
            return False
        return self._send(peer, payload, channel)

    async def send_srv(self, payload: bytes, client_id: str, channel: str = DATA) -> bool:
        return self.send_srv_nowait(payload, client_id, channel)

    async def recv_srv(self, client_id: str, channel: str = DATA) -> bytes:
        _check_channel(channel)
        return (await self._peer(client_id).inbox.take(channel))[1]

    async def recv_any_srv(self, client_id: str) -> tuple[str, bytes]:
        return await self._peer(client_id).inbox.take()


# ---------------------------------------------------------------------------
# QUIC
# ---------------------------------------------------------------------------

ALPN = "gabriel"
# First byte of every stream.
_DATA_STREAM = 0x00  # one data payload: channel name, then payload to the end
_CONTROL_STREAM = 0x01  # long-lived: length-prefixed control messages
_HELLO = 0xFF
_CONTROL_LEN = struct.Struct("<I")


def _split_endpoint(endpoint: str) -> tuple[str, int]:
    """Split ``host:port`` (or ``[v6addr]:port``) into its parts."""
    host, _, port = endpoint.rpartition(":")
    return host.strip("[]"), int(port)


class _QuicPeer:
    """State for one QUIC connection, shared by the client and server sides.

    Each data payload travels on a fresh unidirectional stream that ends with
    the payload, so a stream is a complete message and the receiver can time
    it from first to last byte. Per channel, at most one payload is being
    written at a time; a newer one waits in `outbox` (overwriting any older
    waiting one) until the previous payload has been handed to the socket.
    That keeps data out of aioquic's and the OS's buffers, so it is as fresh
    as possible when it leaves. Channels are independent of each other.

    Control messages share one long-lived unidirectional stream per
    direction, so they are reliable and ordered.
    """

    def __init__(self, protocol: "_QuicProtocol"):
        self.protocol = protocol
        self.client_id: str | None = None
        self.closed = False
        self.inbox = _Inbox()
        self.outbox: dict[str, bytes] = {}
        self.in_flight: dict[str, int] = {}
        # Newest stream delivered per channel; older streams that complete
        # later are stale and dropped.
        self.newest: dict[str, int] = {}
        self.partial: dict[int, bytearray] = {}
        self.first_byte: dict[int, float] = {}
        self.control_out: int | None = None
        self.control_in: dict[int, bytearray] = {}

    def _has_left(self, stream_id: int) -> bool:
        stream = self.protocol._quic._streams.get(stream_id)
        return stream is None or stream.sender.buffer_is_empty

    def write(self, frame: bytes) -> int:
        quic = self.protocol._quic
        stream_id = quic.get_next_available_stream_id(is_unidirectional=True)
        quic.send_stream_data(stream_id, frame, end_stream=True)
        return stream_id

    def write_control(self, payload: bytes):
        quic = self.protocol._quic
        data = _CONTROL_LEN.pack(len(payload)) + payload
        if self.control_out is None:
            self.control_out = quic.get_next_available_stream_id(is_unidirectional=True)
            data = bytes([_CONTROL_STREAM]) + data
        quic.send_stream_data(self.control_out, data, end_stream=False)

    def pump(self) -> bool:
        """Write waiting payloads on channels that have no payload in flight.

        Returns:
            bool: True if anything was written
        """
        if self.closed:
            return False
        wrote = False
        for channel in list(self.outbox):
            stream_id = self.in_flight.get(channel)
            if stream_id is not None and not self._has_left(stream_id):
                continue
            payload = self.outbox.pop(channel)
            self.in_flight[channel] = self.write(
                bytes([_DATA_STREAM]) + _frame_channel(channel, payload)
            )
            wrote = True
        for channel, stream_id in list(self.in_flight.items()):
            if channel not in self.outbox and self._has_left(stream_id):
                del self.in_flight[channel]
        return wrote

    def close(self):
        self.closed = True
        self.partial.clear()
        self.first_byte.clear()
        self.inbox.close()


class _QuicProtocol(QuicConnectionProtocol):
    """aioquic protocol for one connection; forwards events to its transport."""

    def __init__(self, *args, owner: "QuicTransport", **kwargs):
        super().__init__(*args, **kwargs)
        self.owner = owner
        self.peer = _QuicPeer(self)

    def quic_event_received(self, event):
        self.owner._on_event(self.peer, event)

    def transmit(self):
        # transmit runs after every received datagram (including acks) and
        # timer, which is exactly when an in-flight payload may have left.
        super().transmit()
        if self.peer.pump():
            super().transmit()


class QuicTransport(Transport):
    """Connects over QUIC.

    Attributes:
        idle_timeout (float): seconds without packets before the connection is
            declared lost; the client sends keepalive pings well within it
    """

    CONNECT_TIMEOUT = 5.0

    def __init__(
        self,
        server_creds: tuple[str, str] | None = None,
        client_id: str | None = None,
        idle_timeout: float = 2.0,
        congestion_control: str = "cubic",
    ):
        super().__init__(client_id)
        # We still need these to present to the client since SSL is required
        # by QUIC; these can be dummy files though
        self.server_creds = server_creds
        self.idle_timeout = idle_timeout
        self.congestion_control = congestion_control
        # Client state
        self._connection_ctx = None
        self._peer: _QuicPeer | None = None
        self._keepalive = None
        # Server state
        self._server = None
        self._clients: dict[str, _QuicPeer] = {}
        self._new_clients: asyncio.Queue[str] = asyncio.Queue()

    def _configuration(self, is_client: bool) -> QuicConfiguration:
        return QuicConfiguration(
            is_client=is_client,
            alpn_protocols=[ALPN],
            idle_timeout=self.idle_timeout,
            congestion_control_algorithm=self.congestion_control,
        )

    async def connect(self, endpoint: str) -> bool:
        host, port = _split_endpoint(endpoint)
        configuration = self._configuration(is_client=True)
        configuration.verify_mode = ssl.CERT_NONE

        self._connection_ctx = connect(
            host,
            port,
            configuration=configuration,
            create_protocol=partial(_QuicProtocol, owner=self),
            wait_connected=True,
        )
        try:
            protocol = await asyncio.wait_for(
                self._connection_ctx.__aenter__(), self.CONNECT_TIMEOUT
            )
        except (ConnectionError, OSError, TimeoutError):
            self._connection_ctx = None
            return False

        self._peer = protocol.peer
        self._peer.write(bytes([_HELLO]) + self.client_id.encode())
        protocol.transmit()
        self._keepalive = asyncio.create_task(self._send_keepalives(protocol))
        return True

    async def _send_keepalives(self, protocol: _QuicProtocol):
        while not protocol.peer.closed:
            protocol._quic.send_ping(0)
            protocol.transmit()
            await asyncio.sleep(self.idle_timeout / 4)

    async def is_connected(self, client_id: str | None = None) -> bool:
        if self._server is not None:
            if client_id is None:
                return True
            peer = self._clients.get(client_id)
            return peer is not None and not peer.closed
        return self._peer is not None and not self._peer.closed

    async def bind(self, endpoint: str) -> bool:
        host, port = _split_endpoint(endpoint)
        configuration = self._configuration(is_client=False)
        # Quic always uses certs, must be pre-generated for testing
        configuration.load_cert_chain(
            certfile=self.server_creds[0],
            keyfile=self.server_creds[1],
        )
        try:
            self._server = await serve(
                host,
                port,
                configuration=configuration,
                create_protocol=partial(_QuicProtocol, owner=self),
            )
        except OSError:
            return False
        return True

    async def accept(self) -> str:
        return await self._new_clients.get()

    def _on_event(self, peer: _QuicPeer, event):
        if isinstance(event, StreamDataReceived):
            sid = event.stream_id
            if sid in peer.control_in:
                self._on_control_bytes(peer, sid, event.data)
                return
            buffer = peer.partial.get(sid)
            if buffer is None:
                if not event.data:
                    return
                if event.data[0] == _CONTROL_STREAM:
                    peer.control_in[sid] = bytearray()
                    self._on_control_bytes(peer, sid, event.data[1:])
                    return
                buffer = peer.partial[sid] = bytearray()
                peer.first_byte[sid] = asyncio.get_running_loop().time()
            buffer += event.data
            if event.end_stream:
                del peer.partial[sid]
                first = peer.first_byte.pop(sid)
                elapsed = asyncio.get_running_loop().time() - first
                self._on_message(peer, sid, bytes(buffer), elapsed)
        elif isinstance(event, StreamReset):
            peer.partial.pop(event.stream_id, None)
            peer.first_byte.pop(event.stream_id, None)
        elif isinstance(event, ConnectionTerminated):
            peer.close()
            if peer.client_id is not None and self._clients.get(peer.client_id) is peer:
                del self._clients[peer.client_id]

    def _on_control_bytes(self, peer: _QuicPeer, stream_id: int, data: bytes):
        buffer = peer.control_in[stream_id]
        buffer += data
        while len(buffer) >= _CONTROL_LEN.size:
            (length,) = _CONTROL_LEN.unpack_from(buffer)
            end = _CONTROL_LEN.size + length
            if len(buffer) < end:
                break
            message = bytes(buffer[_CONTROL_LEN.size:end])
            del buffer[:end]
            peer.inbox.put(CONTROL, message)

    def _on_message(self, peer: _QuicPeer, stream_id: int, message: bytes, elapsed: float):
        if not message:
            return
        kind, body = message[0], message[1:]
        if kind == _HELLO:
            self._register(peer, body.decode())
        elif kind == _DATA_STREAM:
            channel, payload = _unframe_channel(body)
            if self.on_frame is not None:
                self.on_frame(peer.client_id, channel, len(message), elapsed)
            if stream_id < peer.newest.get(channel, -1):
                return  # an older payload finished after a newer one
            peer.newest[channel] = stream_id
            if peer.inbox.put(channel, payload):
                self.recv_overwrites += 1

    def _register(self, peer: _QuicPeer, client_id: str):
        if self._server is None or peer.client_id is not None:
            return
        peer.client_id = client_id
        old = self._clients.get(client_id)
        if old is not None and old is not peer:
            # The client reconnected; the old connection is dead to us.
            old.close()
            old.protocol.close()
        self._clients[client_id] = peer
        self._new_clients.put_nowait(client_id)

    def _send(self, peer: _QuicPeer | None, payload: bytes, channel: str) -> bool:
        _check_channel(channel)
        if peer is None or peer.closed:
            return False
        if channel == CONTROL:
            peer.write_control(payload)
        else:
            replaced = peer.outbox.get(channel)
            peer.outbox[channel] = payload
            if replaced is not None:
                self.send_overwrites += 1
                if self.on_replace is not None:
                    self.on_replace(peer.client_id, channel, replaced)
        peer.protocol.transmit()
        return True

    async def close(self):
        if self._keepalive is not None:
            self._keepalive.cancel()
            self._keepalive = None
        if self._connection_ctx is not None:
            ctx, self._connection_ctx = self._connection_ctx, None
            try:
                await asyncio.wait_for(ctx.__aexit__(None, None, None), 1.0)
            except (TimeoutError, ConnectionError, OSError):
                pass
        if self._peer is not None:
            self._peer.close()
        if self._server is not None:
            for peer in list(self._clients.values()):
                peer.close()
                peer.protocol.close()
            self._clients.clear()
            self._server.close()
            self._server = None

    def send_cli_nowait(self, payload: bytes, channel: str = DATA) -> bool:
        return self._send(self._peer, payload, channel)

    async def send_cli(self, payload: bytes, channel: str = DATA) -> bool:
        return self.send_cli_nowait(payload, channel)

    async def recv_cli(self, channel: str = DATA) -> bytes:
        _check_channel(channel)
        if self._peer is None:
            raise ConnectionError("not connected")
        return (await self._peer.inbox.take(channel))[1]

    async def recv_any_cli(self) -> tuple[str, bytes]:
        if self._peer is None:
            raise ConnectionError("not connected")
        return await self._peer.inbox.take()

    def send_srv_nowait(self, payload: bytes, client_id: str, channel: str = DATA) -> bool:
        return self._send(self._clients.get(client_id), payload, channel)

    async def send_srv(self, payload: bytes, client_id: str, channel: str = DATA) -> bool:
        return self.send_srv_nowait(payload, client_id, channel)

    def _client_peer(self, client_id: str) -> _QuicPeer:
        peer = self._clients.get(client_id)
        if peer is None:
            raise ConnectionError(f"client {client_id!r} is not connected")
        return peer

    async def recv_srv(self, client_id: str, channel: str = DATA) -> bytes:
        _check_channel(channel)
        return (await self._client_peer(client_id).inbox.take(channel))[1]

    async def recv_any_srv(self, client_id: str) -> tuple[str, bytes]:
        return await self._client_peer(client_id).inbox.take()
