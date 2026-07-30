"""Tests exercising the different client<->server transports.

Covers ZeroMQ (TCP and IPC), WebSocket, gRPC, and local (in-process)
transports, plus multi-engine targeting and mid-session
disconnection/reconnection.
"""

import asyncio
import contextlib
import logging

import pytest
from gabriel_client.grpc_client import GrpcClient
from gabriel_client.websocket_client import WebsocketClient
from gabriel_client.zeromq_client import ZeroMQClient
from gabriel_protocol import gabriel_pb2
from gabriel_server.local_engine import LocalEngine
from gabriel_server.network_engine.server_runner import Transport
from helpers import (
    DEFAULT_NUM_TOKENS,
    DEFAULT_SERVER_HOST,
    INPUT_QUEUE_MAXSIZE,
    Engine,
    cancel_and_wait,
    get_consumer,
    get_multiple_engine_consumer,
    wait_until,
)

logger = logging.getLogger(__name__)


@pytest.mark.asyncio
async def test_zeromq_client(
    run_engines,
    input_producer,
    server_frontend_port,
    response_state,
    prometheus_client_port,
):
    """Test that the ZeroMQ client can connect to a server."""
    response_state.clear()
    response_state["received"] = False

    client = ZeroMQClient(
        f"tcp://{DEFAULT_SERVER_HOST}:{server_frontend_port}",
        input_producer,
        get_consumer(response_state),
        prometheus_client_port,
    )
    task = asyncio.create_task(client.launch_async())

    await wait_until(lambda: response_state["received"])
    await cancel_and_wait(task)

    assert response_state["received"]

    result = response_state["result"]
    assert result.status.code == gabriel_pb2.StatusCode.SUCCESS
    assert result.WhichOneof("payload") == "string_result"
    assert result.string_result == "hello"


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", [Transport.WEBSOCKET])
async def test_websocket_client(
    run_engines, input_producer, response_state, server_frontend_port
):
    """Test that the WebSocket client can connect to a server."""
    response_state.clear()
    response_state["received"] = False

    client = WebsocketClient(
        f"ws://{DEFAULT_SERVER_HOST}:{server_frontend_port}",
        input_producer,
        get_consumer(response_state),
    )
    task = asyncio.create_task(client.launch_async())

    await wait_until(lambda: response_state["received"])
    await cancel_and_wait(task)

    assert response_state["received"]


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", [Transport.GRPC])
async def test_grpc_client(
    run_engines,
    input_producer,
    server_frontend_port,
    response_state,
    prometheus_client_port,
):
    """Test that the gRPC client can connect to a server."""
    response_state.clear()
    response_state["received"] = False

    client = GrpcClient(
        f"localhost:{server_frontend_port}",
        input_producer,
        get_consumer(response_state),
        prometheus_port=prometheus_client_port,
    )
    task = asyncio.create_task(client.launch_async())

    await wait_until(lambda: response_state["received"])
    await cancel_and_wait(task)

    assert response_state["received"]

    result = response_state["result"]
    assert result.status.code == gabriel_pb2.StatusCode.SUCCESS
    assert result.WhichOneof("payload") == "string_result"
    assert result.string_result == "hello"


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", [Transport.GRPC])
@pytest.mark.parametrize(
    "target_engines",
    [
        ["Engine-0"],
        ["Engine-0", "Engine-1"],
        ["Engine-0", "Engine-1", "Engine-2"],
    ],
)
@pytest.mark.parametrize("num_engines", [3])
async def test_grpc_client_send_multiple_engines(
    input_producer,
    server_frontend_port,
    target_engines,
    run_engines,
    response_state,
    prometheus_client_port,
):
    """Test that the gRPC client receives a response from targeted engines."""
    response_state.clear()

    client = GrpcClient(
        f"localhost:{server_frontend_port}",
        input_producer,
        get_multiple_engine_consumer(response_state),
        prometheus_port=prometheus_client_port,
    )
    asyncio.create_task(client.launch_async())

    await wait_until(
        lambda: len(response_state) == len(target_engines), timeout=5
    )

    assert len(response_state) == len(target_engines)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "target_engines",
    [
        ["Engine-0"],
        ["Engine-0", "Engine-1"],
        ["Engine-0", "Engine-1", "Engine-2"],
    ],
)
@pytest.mark.parametrize("num_engines", [3])
async def test_send_multiple_engines(
    input_producer,
    server_frontend_port,
    target_engines,
    run_engines,
    response_state,
    prometheus_client_port,
):
    """Test that we receive a response from each engine we target."""
    response_state.clear()

    client = ZeroMQClient(
        f"tcp://{DEFAULT_SERVER_HOST}:{server_frontend_port}",
        input_producer,
        get_multiple_engine_consumer(response_state),
        prometheus_client_port,
    )
    task = asyncio.create_task(client.launch_async())

    with contextlib.suppress(TimeoutError, asyncio.TimeoutError):
        await asyncio.wait_for(task, timeout=1)

    assert len(response_state) == len(target_engines)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "target_engines",
    [
        ["Engine-0"],
        ["Engine-0", "Engine-1"],
        ["Engine-0", "Engine-1", "Engine-2"],
    ],
)
@pytest.mark.parametrize("num_engines", [3])
@pytest.mark.parametrize("use_client_ipc", [True])
async def test_send_multiple_engines_ipc(
    input_producer,
    server_frontend_port,
    target_engines,
    run_engines,
    response_state,
    prometheus_client_port,
    client_ipc_path,
):
    """Test that we receive a response from each engine we target using IPC."""
    response_state.clear()

    client = ZeroMQClient(
        f"ipc://{client_ipc_path}",
        input_producer,
        get_multiple_engine_consumer(response_state),
        prometheus_client_port,
    )
    task = asyncio.create_task(client.launch_async())

    with contextlib.suppress(TimeoutError, asyncio.TimeoutError):
        await asyncio.wait_for(task, timeout=1)
    assert len(response_state) == len(target_engines)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "target_engines",
    [
        ["Engine-0"],
        ["Engine-0", "Engine-1"],
        ["Engine-0", "Engine-1", "Engine-2"],
    ],
)
@pytest.mark.parametrize("num_engines", [3])
@pytest.mark.parametrize("use_engine_ipc", [True])
async def test_send_multiple_engines_engine_ipc(
    input_producer,
    server_frontend_port,
    target_engines,
    run_engines,
    response_state,
    prometheus_client_port,
):
    """Test that engines can connect over a Unix domain socket."""
    response_state.clear()

    client = ZeroMQClient(
        f"tcp://{DEFAULT_SERVER_HOST}:{server_frontend_port}",
        input_producer,
        get_multiple_engine_consumer(response_state),
        prometheus_client_port,
    )
    task = asyncio.create_task(client.launch_async())

    with contextlib.suppress(TimeoutError, asyncio.TimeoutError):
        await asyncio.wait_for(task, timeout=1)
    assert len(response_state) == len(target_engines)


@pytest.mark.asyncio
@pytest.mark.parametrize("target_engines", [["local_engine"]])
async def test_local_server(
    input_producer,
    server_frontend_port,
    response_state,
    prometheus_client_port,
):
    """Test that we can run a local engine with zeromq."""
    response_state.clear()
    response_state["received"] = False

    engine = LocalEngine(
        lambda: Engine(0, None),
        port=server_frontend_port,
        num_tokens=DEFAULT_NUM_TOKENS,
        input_queue_maxsize=INPUT_QUEUE_MAXSIZE,
        use_zeromq=True,
    )
    engine_task = asyncio.create_task(engine.run_async())
    await asyncio.sleep(0)

    client = ZeroMQClient(
        f"tcp://{DEFAULT_SERVER_HOST}:{server_frontend_port}",
        input_producer,
        get_consumer(response_state),
        prometheus_client_port,
    )
    client_task = asyncio.create_task(client.launch_async())

    if not await wait_until(lambda: response_state["received"]):
        logger.error("Did not receive response from local engine")

    engine_task.cancel()
    await cancel_and_wait(client_task)
    await cancel_and_wait(engine_task)

    assert response_state["received"]


@pytest.mark.asyncio
@pytest.mark.parametrize("target_engines", [["local_engine"]])
async def test_ipc_local_engine(
    input_producer,
    server_frontend_port,
    response_state,
    prometheus_client_port,
    client_ipc_path,
):
    """Test that we can run a local engine with ipc."""
    response_state.clear()
    response_state["received"] = False

    engine = LocalEngine(
        lambda: Engine(0, None),
        port=server_frontend_port,
        num_tokens=DEFAULT_NUM_TOKENS,
        input_queue_maxsize=INPUT_QUEUE_MAXSIZE,
        use_zeromq=True,
        ipc_path=str(client_ipc_path),
    )
    engine_task = asyncio.create_task(engine.run_async())
    await asyncio.sleep(0)

    client = ZeroMQClient(
        f"ipc://{client_ipc_path}",
        input_producer,
        get_consumer(response_state),
        prometheus_client_port,
    )
    client_task = asyncio.create_task(client.launch_async())

    if not await wait_until(lambda: response_state["received"]):
        logger.error("Did not receive response from local engine")

    engine_task.cancel()
    await cancel_and_wait(client_task)
    await cancel_and_wait(engine_task)

    assert response_state["received"]


@pytest.mark.asyncio
@pytest.mark.parametrize("num_engines", [3])
async def test_change_target_engines(
    input_producer,
    server_frontend_port,
    target_engines,
    run_engines,
    response_state,
    prometheus_client_port,
):
    """Test that we can change the target engines on the fly."""
    response_state.clear()

    client = ZeroMQClient(
        f"tcp://{DEFAULT_SERVER_HOST}:{server_frontend_port}",
        input_producer,
        get_multiple_engine_consumer(response_state),
        prometheus_client_port,
    )
    task = asyncio.create_task(client.launch_async())

    with contextlib.suppress(TimeoutError, asyncio.TimeoutError):
        await asyncio.wait_for(asyncio.shield(task), timeout=1)

    assert len(response_state) == 1

    input_producer[0].change_target_engines(
        target_engine_ids=["Engine-0", "Engine-1"]
    )

    with contextlib.suppress(TimeoutError, asyncio.TimeoutError):
        await asyncio.wait_for(task, timeout=1)

    assert len(response_state) == 2


@pytest.mark.asyncio
async def test_stop_producer(
    run_engines,
    input_producer,
    server_frontend_port,
    response_state,
    prometheus_client_port,
):
    """Test that stopping the input producer stops inputs from being sent."""
    response_state.clear()

    client = ZeroMQClient(
        f"tcp://{DEFAULT_SERVER_HOST}:{server_frontend_port}",
        input_producer,
        get_multiple_engine_consumer(response_state),
        prometheus_client_port,
    )
    task = asyncio.create_task(client.launch_async())

    await wait_until(lambda: len(response_state) == 1, timeout=5)
    assert len(response_state) == 1
    num_responses = response_state["Engine-0"]

    logger.info("Stopping input producer")
    input_producer[0].stop()
    # Negative assertion: wait a fixed window (no condition to poll for),
    # then confirm the count didn't keep climbing.
    await asyncio.sleep(1)
    assert response_state["Engine-0"] - num_responses <= 1

    logger.info("Resuming input producer")
    input_producer[0].resume()
    await wait_until(
        lambda: response_state["Engine-0"] - num_responses > 1, timeout=5
    )
    assert response_state["Engine-0"] - num_responses > 1

    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_disconnection(
    input_producer,
    server_frontend_port,
    target_engines,
    run_engines,
    response_state,
    run_server,
    prometheus_client_port,
):
    """Test that the client can handle server disconnection."""
    response_state.clear()

    client = ZeroMQClient(
        f"tcp://{DEFAULT_SERVER_HOST}:{server_frontend_port}",
        input_producer,
        get_multiple_engine_consumer(response_state),
        prometheus_client_port,
    )
    task = asyncio.create_task(client.launch_async())

    with contextlib.suppress(TimeoutError, asyncio.TimeoutError):
        await asyncio.wait_for(asyncio.shield(task), timeout=1)

    assert len(response_state) == 1

    # Simulate server disconnection
    logger.debug("Simulating disconnection")
    server = run_server.server
    await server._close_server_socket()
    await asyncio.sleep(12)
    num_responses = response_state["Engine-0"]

    # Restart server
    await server._recreate_server_socket()
    await asyncio.sleep(2)
    logger.info(f"{response_state=}")
    assert response_state["Engine-0"] > num_responses

    logger.info("Cancelling handler task")
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", [Transport.GRPC])
async def test_grpc_disconnection(
    input_producer,
    server_frontend_port,
    target_engines,
    run_engines,
    response_state,
    run_server,
    prometheus_client_port,
):
    """Test that the gRPC client reconnects after a server disconnection."""
    response_state.clear()

    client = GrpcClient(
        f"localhost:{server_frontend_port}",
        input_producer,
        get_multiple_engine_consumer(response_state),
        prometheus_port=prometheus_client_port,
    )
    task = asyncio.create_task(client.launch_async())

    await wait_until(lambda: len(response_state) == 1, timeout=5)
    assert len(response_state) == 1

    # Simulate server disconnection: stopping the gRPC server tears down
    # every open client stream, which the client should detect and, per its
    # reconnect-on-disconnection behavior, retry against.
    logger.debug("Simulating disconnection")
    server = run_server.server
    await server._close_server_socket()
    await asyncio.sleep(12)
    num_responses = response_state["Engine-0"]

    # Restart server. The client retries on a fixed interval
    # (RECONNECT_INTERVAL_SECONDS in grpc_client.py), so poll rather than
    # sleeping a fixed amount: a flat sleep barely longer than that interval
    # leaves no margin if a retry lands just before the server finishes
    # rebinding.
    await server._recreate_server_socket()
    await wait_until(
        lambda: response_state["Engine-0"] > num_responses, timeout=15
    )
    logger.info(f"{response_state=}")
    assert response_state["Engine-0"] > num_responses

    logger.info("Cancelling handler task")
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
