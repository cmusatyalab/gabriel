"""Tests covering engine registration/disconnection and error handling.

Covers: targeting an engine that isn't connected, engines returning bad values
from handle(), an engine disconnecting/reconnecting mid-session, duplicate
engine ids, and the ZeroMQ result-sink pipeline.
"""

import asyncio
import contextlib
import logging
import time

import pytest
import zmq
import zmq.asyncio
from gabriel_client.zeromq_client import ZeroMQClient
from gabriel_protocol import gabriel_pb2
from gabriel_server import cognitive_engine
from gabriel_server.cognitive_engine import Result
from gabriel_server.result_manager import ZeroMQSink
from google.protobuf import any_pb2, wrappers_pb2
from helpers import (
    DEFAULT_SERVER_HOST,
    Engine,
    cancel_and_wait,
    get_consumer,
    get_multiple_engine_consumer,
    wait_until,
)

logger = logging.getLogger(__name__)


@pytest.mark.parametrize("target_engines", [["invalid_engine"]])
@pytest.mark.asyncio
async def test_invalid_engine(
    run_engines,
    input_producer,
    server_frontend_port,
    prometheus_client_port,
):
    """Test that an invalid engine ID raises an error."""
    client = ZeroMQClient(
        f"tcp://{DEFAULT_SERVER_HOST}:{server_frontend_port}",
        input_producer,
        lambda x: x,
        prometheus_client_port,
    )
    task = asyncio.create_task(client.launch_async())
    await wait_until(task.done, timeout=5)
    exceptions = await asyncio.gather(task, return_exceptions=True)

    assert len(exceptions) == 1
    exception = exceptions[0]
    assert isinstance(exception, Exception)
    assert (
        "Attempt to target engines that are not connected to the server: "
        "{'invalid_engine'}" in str(exception)
    )


@pytest.mark.asyncio
async def test_empty_input_frame(
    run_engines,
    empty_frame_producer,
    server_frontend_port,
    response_state,
    caplog,
    prometheus_client_port,
):
    """Test that an error is raised when an empty frame is produced."""
    response_state.clear()
    response_state["received"] = False

    client = ZeroMQClient(
        f"tcp://{DEFAULT_SERVER_HOST}:{server_frontend_port}",
        empty_frame_producer,
        get_consumer(response_state),
        prometheus_client_port,
    )
    task = asyncio.create_task(client.launch_async())

    await wait_until(lambda: response_state["received"])
    await cancel_and_wait(task)

    assert "Input producer produced empty frame" in caplog.text
    assert not response_state["received"]


def bad_handle_none(self, input_frame, client_info):
    """An engine handler that returns None."""
    return None


def bad_handle_status(self, input_frame, client_info):
    """An engine handler that returns a None status."""
    status = None
    return Result(status, "hello")


@pytest.mark.asyncio
async def test_engine_return_none(
    run_engines,
    input_producer,
    server_frontend_port,
    response_state,
    monkeypatch,
    caplog,
    prometheus_client_port,
):
    """Test for error when an engine returns None."""
    response_state.clear()
    response_state["received"] = False

    monkeypatch.setattr(Engine, "handle", bad_handle_none)

    client = ZeroMQClient(
        f"tcp://{DEFAULT_SERVER_HOST}:{server_frontend_port}",
        input_producer,
        lambda x: x,
        prometheus_client_port,
    )
    task = asyncio.create_task(client.launch_async())
    await wait_until(
        lambda: "Incorrect type returned by engine" in caplog.text, timeout=5
    )
    await cancel_and_wait(task)

    assert "Incorrect type returned by engine" in caplog.text
    assert (
        "targeting engine Engine-0 caused error ENGINE_ERROR: Incorrect type "
        "returned by engine. Expected a value of type cognitive_engine.Result"
        ", found <class 'NoneType'>"
    ) in caplog.text
    assert not response_state["received"]


@pytest.mark.asyncio
async def test_engine_return_bad_status(
    run_engines,
    input_producer,
    server_frontend_port,
    response_state,
    monkeypatch,
    caplog,
    prometheus_client_port,
):
    """Test for error when an engine returns an invalid status."""
    response_state.clear()
    response_state["received"] = False

    monkeypatch.setattr(Engine, "handle", bad_handle_status)

    client = ZeroMQClient(
        f"tcp://{DEFAULT_SERVER_HOST}:{server_frontend_port}",
        input_producer,
        lambda x: x,
        prometheus_client_port,
    )
    task = asyncio.create_task(client.launch_async())
    await wait_until(
        lambda: "Return status not populated correctly by engine"
        in caplog.text,
        timeout=5,
    )
    await cancel_and_wait(task)

    assert "Return status not populated correctly by engine" in caplog.text
    assert (
        "targeting engine Engine-0 caused error ENGINE_ERROR: Return status "
        "not populated correctly by engine. Expected a value of type "
        "gabriel_pb2.Status, found <class 'NoneType'>"
    ) in caplog.text
    assert not response_state["received"]


def bad_handle_raises(self, input_frame, client_info):
    """An engine handler that always raises, simulating an engine crash."""
    raise RuntimeError("Simulated engine crash")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "target_engines", [["Engine-0", "Engine-1", "Engine-2"]]
)
@pytest.mark.parametrize("num_engines", [3])
async def test_all_target_engines_crashing_returns_token_once(
    run_server,
    run_engines,
    input_producer,
    server_frontend_port,
    target_engines,
    caplog,
    monkeypatch,
    prometheus_client_port,
):
    """Single token should be returned if every targeted engine crashes.

    Regression test: when all engines targeted by an input disconnect, the
    server should release only one token back to the client.
    """
    monkeypatch.setattr(Engine, "handle", bad_handle_raises)

    client = ZeroMQClient(
        f"tcp://{DEFAULT_SERVER_HOST}:{server_frontend_port}",
        input_producer,
        lambda x: x,
        prometheus_client_port,
    )
    task = asyncio.create_task(client.launch_async())

    await wait_until(
        lambda: not (set(target_engines) & run_server.server._engine_ids),
        timeout=5,
    )

    # A crashing handle() permanently disconnects all target engines, so the
    # client will eventually raise a "not connected" error once it tries to
    # target them again. What must NOT happen is the BoundedSemaphore being
    # over-released.
    try:
        await asyncio.wait_for(task, timeout=5)
    except (TimeoutError, asyncio.TimeoutError):
        pass
    except Exception as e:
        assert "released too many times" not in str(e)
        assert "BoundedSemaphore" not in str(e)

    assert "released too many times" not in caplog.text

    if not task.done():
        await cancel_and_wait(task)


def crash_second_engine_after_first_responds(self, input_frame, client_info):
    """Engine-0 succeeds immediately; Engine-1 crashes shortly after.

    The delay lets Engine-0's result reach the server (and claim the token
    for the frame) before Engine-1 disconnects, so Engine-1's disconnect is
    handled with the token already returned.
    """
    if self.engine_id == 1:
        time.sleep(0.3)
        raise RuntimeError("Simulated engine crash")
    status = gabriel_pb2.Status()
    status.code = gabriel_pb2.StatusCode.SUCCESS
    return Result(status, "hello")


@pytest.mark.asyncio
@pytest.mark.parametrize("target_engines", [["Engine-0", "Engine-1"]])
@pytest.mark.parametrize("engine_ids", [[0, 1]])
@pytest.mark.parametrize("num_engines", [2])
@pytest.mark.parametrize("num_inputs_to_send", [1])
async def test_engine_disconnect_after_token_returned_still_notifies_client(
    run_server,
    run_engines,
    input_producer,
    server_frontend_port,
    response_state,
    monkeypatch,
    caplog,
    prometheus_client_port,
):
    """Test client is notified of a disconnected targeted engine."""
    monkeypatch.setattr(
        Engine, "handle", crash_second_engine_after_first_responds
    )

    client = ZeroMQClient(
        f"tcp://{DEFAULT_SERVER_HOST}:{server_frontend_port}",
        input_producer,
        get_multiple_engine_consumer(response_state),
        prometheus_client_port,
    )
    task = asyncio.create_task(client.launch_async())

    await wait_until(
        lambda: "Engine-1" not in run_server.server._engine_ids, timeout=5
    )
    await wait_until(
        lambda: "targeting engine Engine-1 caused error ENGINE_ERROR: "
        "Engine Engine-1 disconnected" in caplog.text,
        timeout=5,
    )

    assert response_state.get("Engine-0", 0) > 0
    assert (
        "targeting engine Engine-1 caused error ENGINE_ERROR: "
        "Engine Engine-1 disconnected" in caplog.text
    )

    if not task.done():
        await cancel_and_wait(task)


@pytest.mark.asyncio
async def test_client_info_propagated_to_engine(
    run_engines,
    input_producer,
    server_frontend_port,
    response_state,
    monkeypatch,
    prometheus_client_port,
):
    """Test that client_info set on the client reaches the engine."""
    received_client_info = []

    def capture_client_info_handle(self, input_frame, client_info):
        received_client_info.append(client_info)
        status = gabriel_pb2.Status()
        status.code = gabriel_pb2.StatusCode.SUCCESS
        return Result(status, "hello")

    monkeypatch.setattr(Engine, "handle", capture_client_info_handle)

    client_info = any_pb2.Any()
    client_info.Pack(wrappers_pb2.StringValue(value="test-client-info"))

    client = ZeroMQClient(
        f"tcp://{DEFAULT_SERVER_HOST}:{server_frontend_port}",
        input_producer,
        get_consumer(response_state),
        prometheus_client_port,
        client_info=client_info,
    )
    task = asyncio.create_task(client.launch_async())

    await wait_until(lambda: len(received_client_info) > 0)
    await cancel_and_wait(task)

    assert len(received_client_info) > 0
    unpacked = wrappers_pb2.StringValue()
    assert received_client_info[0].Unpack(unpacked)
    assert unpacked.value == "test-client-info"


@pytest.mark.asyncio
@pytest.mark.parametrize("target_engines", [[]])
async def test_target_no_engines(
    run_engines,
    input_producer,
    server_frontend_port,
    target_engines,
    caplog,
    prometheus_client_port,
):
    """Test that an exception is thrown if a client targets no engines."""
    client = ZeroMQClient(
        f"tcp://{DEFAULT_SERVER_HOST}:{server_frontend_port}",
        input_producer,
        lambda x: x,
        prometheus_client_port,
    )
    task = asyncio.create_task(client.launch_async())

    await wait_until(
        lambda: "None targets no engines" in caplog.text, timeout=5
    )

    assert "None targets no engines" in caplog.text
    assert task.done()


@pytest.mark.asyncio
async def test_new_engine_connected(
    run_server,
    run_engines,
    input_producer,
    server_frontend_port,
    response_state,
    server_backend_port,
    caplog,
    prometheus_client_port,
):
    """Test client is updated when new engine is connected to server."""
    client = ZeroMQClient(
        f"tcp://{DEFAULT_SERVER_HOST}:{server_frontend_port}",
        input_producer,
        get_consumer(response_state),
        prometheus_client_port,
    )
    client_task = asyncio.create_task(client.launch_async())

    await asyncio.sleep(0.1)

    # Launch new engine
    engine_id = 1
    engine_address = f"localhost:{server_backend_port}"
    engine = Engine(engine_id, engine_address)
    engine_task = asyncio.create_task(engine.run_async())

    # Wait for the engine to register with the server and for the client to
    # be notified of it (rather than assuming a fixed delay is enough, since
    # the gRPC engine handshake's latency can vary under load).
    await wait_until(lambda: "Engine-1" in client._engine_ids)
    assert "Engine-1" in client._engine_ids

    input_producer[0].change_target_engines(["Engine-1"])
    response_state.clear()
    response_state["received"] = False

    await wait_until(lambda: response_state["received"])
    assert response_state["received"]

    input_producer[0].stop()

    # stop() only blocks the *next* iteration of the producer loop; a frame
    # that was already being produced when stop() was called can still be sent
    # afterward. Give it time to drain before disconnecting Engine-1, so no
    # frame targeting Engine-1 is in flight while the disconnect propagates
    # (which would otherwise race the client's local "not connected" check
    # against the server's own error response).
    await asyncio.sleep(0.2)

    engine_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await engine_task

    # With the gRPC engine transport, a cancelled engine's stream closes
    # immediately server-side, so disconnection is detected right away rather
    # than only after a heartbeat timeout.
    await wait_until(
        lambda: "Engine-1" not in run_server.server._engine_ids, timeout=5
    )
    assert "Engine-1" not in run_server.server._engine_ids

    # Wait for the client to be notified (via a control message) that Engine-1
    # is no longer connected before resuming input production, so the resumed
    # frame is guaranteed to hit the client's own local "not connected" check
    # rather than racing the server's response.
    await wait_until(lambda: "Engine-1" not in client._engine_ids, timeout=5)

    input_producer[0].resume()

    exceptions = await asyncio.gather(client_task, return_exceptions=True)

    assert len(exceptions) == 1
    exception = exceptions[0]
    assert isinstance(exception, Exception)
    assert (
        "Attempt to target engines that are not connected to the server: "
        "{'Engine-1'}" in str(exception)
    )


def server_dropped_frame_handle(input_frame, client_info):
    """Engine handle method that returns server dropped frame error."""
    status = gabriel_pb2.Status()
    status.code = gabriel_pb2.StatusCode.SERVER_DROPPED_FRAME
    status.message = "Dropping frame at engine"
    return cognitive_engine.Result(status, None)


@pytest.mark.asyncio
@pytest.mark.parametrize("handle_method", [server_dropped_frame_handle])
async def test_server_dropped_frame(
    run_engines,
    input_producer,
    server_frontend_port,
    response_state,
    prometheus_client_port,
    caplog,
):
    """Test that the server dropped frame error is correctly handled."""
    response_state.clear()
    response_state["received"] = False

    client = ZeroMQClient(
        f"tcp://{DEFAULT_SERVER_HOST}:{server_frontend_port}",
        input_producer,
        get_consumer(response_state),
        prometheus_client_port,
    )
    task = asyncio.create_task(client.launch_async())

    # Positive assertion (an error should have been logged), but there's no
    # cheaper condition to poll for than the log line itself, and a fixed
    # window is fine here since SERVER_DROPPED_FRAME is returned immediately
    # by the (synchronous, no-sleep) handler above.
    await asyncio.sleep(0.5)

    assert not task.done()
    await cancel_and_wait(task)

    assert "Engine Engine-0 dropped frame from producer" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("num_engines", [2])
@pytest.mark.parametrize("engine_ids", [[0, 0]])
async def test_duplicate_engine_id(
    run_engines,
    input_producer,
    server_frontend_port,
    response_state,
    prometheus_client_port,
    caplog,
):
    """Test the behavior when multiple engines connect with same engine id."""
    response_state.clear()
    response_state["received"] = False

    client = ZeroMQClient(
        f"tcp://{DEFAULT_SERVER_HOST}:{server_frontend_port}",
        input_producer,
        get_consumer(response_state),
        prometheus_client_port,
    )
    task = asyncio.create_task(client.launch_async())

    # Negative-ish assertion (the server should not have crashed/errored the
    # client out); no useful condition to poll for.
    await asyncio.sleep(0.1)

    assert not task.done()
    await cancel_and_wait(task)


@pytest.mark.asyncio
async def test_zeromq_result_output(
    run_engines,
    run_server,
    input_producer,
    server_frontend_port,
    response_state,
    prometheus_client_port,
    tmp_path,
):
    """Test that the ZeroMQ result pipeline works."""
    server = run_server.server
    result_manager = server.result_manager
    result_ipc_path = tmp_path / "engine_results.ipc"
    zeromq_sink = ZeroMQSink(str(result_ipc_path))

    result_manager.register_result_sink(zeromq_sink)

    # Create a SUBSCRIBE socket
    ctx = zmq.asyncio.Context()
    sock = ctx.socket(zmq.SUB)
    sock.connect(f"ipc://{result_ipc_path}")
    sock.setsockopt(zmq.SUBSCRIBE, b"Engine-0")

    client = ZeroMQClient(
        f"tcp://{DEFAULT_SERVER_HOST}:{server_frontend_port}",
        input_producer,
        get_consumer(response_state),
        prometheus_client_port,
    )
    task = asyncio.create_task(client.launch_async())

    await asyncio.sleep(0.5)

    assert not task.done()
    await cancel_and_wait(task)

    assert await sock.poll(timeout=1) & zmq.POLLIN
    msg = await sock.recv_multipart()
    result = gabriel_pb2.Result()
    result.ParseFromString(msg[1])

    assert result.target_engine_id == "Engine-0"
    assert result.string_result == "hello"
    assert result.frame_id == 1
