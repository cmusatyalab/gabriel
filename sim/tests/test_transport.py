import asyncio
import datetime
import socket
import uuid

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from sim.transport import CONTROL, DATA, IceoryxTransport, QuicTransport


@pytest.fixture(scope="module")
def creds(tmp_path_factory) -> tuple[str, str]:
    """Self-signed certificate for the QUIC server."""
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    directory = tmp_path_factory.mktemp("creds")
    certfile, keyfile = directory / "cert.pem", directory / "key.pem"
    certfile.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    keyfile.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return str(certfile), str(keyfile)


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(params=["iceoryx", "quic"])
def make_pair(request, creds):
    """Returns a factory for (server, client, endpoint) of one transport kind."""
    if request.param == "iceoryx":
        def make():
            endpoint = f"test/{uuid.uuid4().hex}"
            return IceoryxTransport(), IceoryxTransport(), endpoint
    else:
        def make():
            endpoint = f"127.0.0.1:{free_port()}"
            return QuicTransport(server_creds=creds), QuicTransport(), endpoint
    return make


def run(coro):
    return asyncio.run(asyncio.wait_for(coro, 10))


async def connected(make_pair):
    server, client, endpoint = make_pair()
    assert await server.bind(endpoint)
    assert await client.connect(endpoint)
    client_id = await asyncio.wait_for(server.accept(), 2)
    assert client_id == client.client_id
    return server, client, client_id, endpoint


def test_connect_without_server_fails(make_pair):
    async def body():
        _, client, endpoint = make_pair()
        client.CONNECT_TIMEOUT = 0.5
        assert not await client.connect(endpoint)
        assert not await client.is_connected()

    run(body())


def test_round_trip(make_pair):
    async def body():
        server, client, client_id, _ = await connected(make_pair)
        assert await client.is_connected()
        assert await server.is_connected(client_id)

        assert await client.send_cli(b"ping")
        assert await server.recv_srv(client_id) == b"ping"
        assert await server.send_srv(b"pong", client_id)
        assert await client.recv_cli() == b"pong"

        await client.close()
        await server.close()

    run(body())


def test_channels_are_independent(make_pair):
    async def body():
        server, client, client_id, _ = await connected(make_pair)
        await client.send_cli(b"need", channel=CONTROL)
        await client.send_cli(b"frame", channel=DATA)
        assert await server.recv_srv(client_id, channel=CONTROL) == b"need"
        assert await server.recv_srv(client_id, channel=DATA) == b"frame"
        await client.close()
        await server.close()

    run(body())


def test_newest_value_wins(make_pair):
    async def body():
        server, client, client_id, _ = await connected(make_pair)
        for i in range(20):
            await client.send_cli(str(i).encode())
        await asyncio.sleep(0.2)
        assert await server.recv_srv(client_id) == b"19"
        # Nothing older is left behind the newest value.
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(server.recv_srv(client_id), 0.2)
        # Every value but the one consumed was overwritten on some side.
        assert client.send_overwrites + server.recv_overwrites == 19
        await client.close()
        await server.close()

    run(body())


def test_large_payload(make_pair):
    async def body():
        server, client, client_id, _ = await connected(make_pair)
        payload = bytes(range(256)) * 8192  # 2 MiB
        await client.send_cli(payload)
        assert await server.recv_srv(client_id) == payload
        await client.close()
        await server.close()

    run(body())


def test_server_sees_client_leave(make_pair):
    async def body():
        server, client, client_id, _ = await connected(make_pair)
        await client.close()
        with pytest.raises(ConnectionError):
            await server.recv_srv(client_id)
        assert not await server.is_connected(client_id)
        await server.close()

    run(body())


def test_reconnect_starts_clean(make_pair):
    async def body():
        server, client, client_id, endpoint = await connected(make_pair)
        await client.send_cli(b"stale")
        await client.close()
        with pytest.raises(ConnectionError):
            while True:  # drain anything sent before the outage
                await server.recv_srv(client_id)

        again = type(client)(client_id=client_id)
        assert await again.connect(endpoint)
        assert await asyncio.wait_for(server.accept(), 2) == client_id
        await again.send_cli(b"fresh")
        assert await server.recv_srv(client_id) == b"fresh"
        await again.close()
        await server.close()

    run(body())


def test_data_channels_are_independent_slots(make_pair):
    async def body():
        server, client, client_id, _ = await connected(make_pair)
        for i in range(5):
            await client.send_cli(f"a{i}".encode(), channel="flow|x|y")
            await client.send_cli(f"b{i}".encode(), channel="flow|x|z")
        await asyncio.sleep(0.2)
        assert await server.recv_srv(client_id, channel="flow|x|y") == b"a4"
        assert await server.recv_srv(client_id, channel="flow|x|z") == b"b4"
        await client.close()
        await server.close()

    run(body())


def test_control_is_reliable_fifo(make_pair):
    async def body():
        server, client, client_id, _ = await connected(make_pair)
        for i in range(50):
            await client.send_cli(str(i).encode(), channel=CONTROL)
        got = [await server.recv_srv(client_id, channel=CONTROL) for _ in range(50)]
        assert got == [str(i).encode() for i in range(50)]
        await client.close()
        await server.close()

    run(body())


def test_recv_any_prefers_control(make_pair):
    async def body():
        server, client, client_id, _ = await connected(make_pair)
        await client.send_cli(b"frame", channel="f|a|b")
        await client.send_cli(b"demand", channel=CONTROL)
        await asyncio.sleep(0.2)
        assert await server.recv_any_srv(client_id) == (CONTROL, b"demand")
        assert await server.recv_any_srv(client_id) == ("f|a|b", b"frame")
        await server.send_srv(b"down", client_id, channel="f|b|c")
        assert await client.recv_any_cli() == ("f|b|c", b"down")
        await client.close()
        await server.close()

    run(body())


def test_quic_times_frames_on_receive(creds):
    async def body():
        server = QuicTransport(server_creds=creds)
        endpoint = f"127.0.0.1:{free_port()}"
        frames = []
        server.on_frame = lambda cid, chan, size, elapsed: frames.append((cid, chan, size, elapsed))
        assert await server.bind(endpoint)
        client = QuicTransport()
        assert await client.connect(endpoint)
        client_id = await asyncio.wait_for(server.accept(), 2)
        await client.send_cli(bytes(100_000), channel="f|a|b")
        assert len(await server.recv_srv(client_id, channel="f|a|b")) == 100_000
        cid, chan, size, elapsed = frames[-1]
        assert (cid, chan) == (client_id, "f|a|b") and size > 100_000 and elapsed >= 0
        await client.close()
        await server.close()

    run(body())
