"""Regression tests for the token/flow-control semaphore.

These exercise multiple concurrent clients and producers against a set of slow,
variable-latency engines to catch the token-accounting bug where the
semaphore's internal count could exceed its configured limit.
"""

import asyncio
import logging
import random
import threading
import time

import pytest
from gabriel_client.zeromq_client import ZeroMQClient
from gabriel_protocol import gabriel_pb2
from gabriel_server import cognitive_engine
from helpers import DEFAULT_SERVER_HOST, get_multiple_engine_consumer

logger = logging.getLogger(__name__)


def heterogenous_engine_handle(input_frame, client_info):
    """A handle method that sleeps different durations."""
    sleep_duration = random.choice([0.01, 0.02, 0.03])
    time.sleep(sleep_duration)
    logger.info(f"Slept for {sleep_duration} seconds")
    status = gabriel_pb2.Status()
    status.code = gabriel_pb2.StatusCode.SUCCESS

    return cognitive_engine.Result(status, "hello")


@pytest.mark.parametrize("num_engines", [3])
@pytest.mark.parametrize(
    "target_engines", [["Engine-0", "Engine-1", "Engine-2"]]
)
@pytest.mark.parametrize("run_engines_threaded", [True])
@pytest.mark.parametrize("handle_method", [heterogenous_engine_handle])
@pytest.mark.asyncio
async def test_tokens_bug(
    multiple_input_producers,
    server_frontend_port,
    target_engines,
    run_engines,
    response_state,
    prometheus_client_port,
):
    """Test that we never exceed the token semaphore limit."""
    response_state.clear()
    client1 = ZeroMQClient(
        f"tcp://{DEFAULT_SERVER_HOST}:{server_frontend_port}",
        multiple_input_producers,
        get_multiple_engine_consumer(response_state),
        prometheus_client_port,
    )
    task1 = asyncio.create_task(client1.launch_async())

    client2 = ZeroMQClient(
        f"tcp://{DEFAULT_SERVER_HOST}:{server_frontend_port}",
        multiple_input_producers,
        get_multiple_engine_consumer(response_state),
        prometheus_client_port,
    )
    task2 = asyncio.create_task(client2.launch_async())

    await asyncio.sleep(30)

    task1.cancel()
    task2.cancel()
    try:
        logger.info("Waiting for client tasks to cancel")
        await task1
        await task2
    except asyncio.CancelledError:
        task = asyncio.current_task()
        if task is not None and task.cancelled():
            raise
    logger.info("Client tasks are cancelled")

    assert len(response_state) == len(target_engines)


@pytest.mark.parametrize("num_engines", [3])
@pytest.mark.parametrize(
    "target_engines", [["Engine-0", "Engine-1", "Engine-2"]]
)
@pytest.mark.parametrize("run_engines_threaded", [True])
@pytest.mark.parametrize("handle_method", [heterogenous_engine_handle])
@pytest.mark.asyncio
async def test_tokens_bug2(
    multiple_input_producers,
    server_frontend_port,
    target_engines,
    run_engines,
    response_state,
    prometheus_client_port,
):
    """Test that we never exceed the token semaphore limit."""
    response_state.clear()
    client1 = ZeroMQClient(
        f"tcp://{DEFAULT_SERVER_HOST}:{server_frontend_port}",
        multiple_input_producers,
        get_multiple_engine_consumer(response_state),
        prometheus_client_port,
    )
    task1 = asyncio.create_task(client1.launch_async())

    client2 = ZeroMQClient(
        f"tcp://{DEFAULT_SERVER_HOST}:{server_frontend_port}",
        multiple_input_producers,
        get_multiple_engine_consumer(response_state),
        prometheus_client_port,
    )
    task2 = asyncio.create_task(client2.launch_async())

    await asyncio.sleep(60)

    task1.cancel()
    task2.cancel()
    try:
        logger.info("Waiting for client tasks to cancel")
        await task1
        await task2
    except asyncio.CancelledError:
        task = asyncio.current_task()
        if task is not None and task.cancelled():
            raise
    logger.info("Client tasks are cancelled")

    assert len(response_state) == len(target_engines)


@pytest.mark.parametrize("num_engines", [3])
@pytest.mark.parametrize(
    "target_engines", [["Engine-0", "Engine-1", "Engine-2"]]
)
@pytest.mark.parametrize("run_engines_threaded", [True])
@pytest.mark.parametrize("handle_method", [heterogenous_engine_handle])
@pytest.mark.asyncio
async def test_tokens_bug_threaded_client(
    multiple_input_producers,
    server_frontend_port,
    target_engines,
    run_engines,
    response_state,
    prometheus_client_port,
):
    """Test that we never exceed the token semaphore limit."""
    response_state.clear()
    client1 = ZeroMQClient(
        f"tcp://{DEFAULT_SERVER_HOST}:{server_frontend_port}",
        multiple_input_producers,
        get_multiple_engine_consumer(response_state),
        prometheus_client_port,
    )
    t1 = threading.Thread(target=client1.launch, daemon=True)

    client2 = ZeroMQClient(
        f"tcp://{DEFAULT_SERVER_HOST}:{server_frontend_port}",
        multiple_input_producers,
        get_multiple_engine_consumer(response_state),
        prometheus_client_port,
    )
    t2 = threading.Thread(target=client2.launch, daemon=True)
    t1.start()
    t2.start()

    await asyncio.sleep(30)

    client1.stop()
    client2.stop()
    t1.join()
    t2.join()

    assert len(response_state) == len(target_engines)
