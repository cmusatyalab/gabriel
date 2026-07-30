"""Tests that Prometheus metrics are correctly collected."""

import asyncio
import contextlib
import copy
import logging

import pytest
from gabriel_client.zeromq_client import ZeroMQClient
from helpers import DEFAULT_SERVER_HOST, find_value, get_consumer
from prometheus_client import REGISTRY

logger = logging.getLogger(__name__)


@pytest.fixture
def metrics_before():
    """Fixture to capture Prometheus metrics before a test runs."""
    yield copy.deepcopy(list(REGISTRY.collect()))


@pytest.mark.asyncio
@pytest.mark.parametrize("num_inputs_to_send", [5])
async def test_prometheus_server_metrics(
    input_producer,
    server_frontend_port,
    target_engines,
    run_engines,
    response_state,
    prometheus_server_port,
    metrics_before,
    prometheus_client_port,
):
    """Test that Prometheus metrics are being collected at the server."""
    response_state.clear()
    response_state["received"] = False

    client = ZeroMQClient(
        f"tcp://{DEFAULT_SERVER_HOST}:{server_frontend_port}",
        input_producer,
        get_consumer(response_state),
        prometheus_client_port,
    )
    task = asyncio.create_task(client.launch_async())

    with contextlib.suppress(TimeoutError, asyncio.TimeoutError):
        await asyncio.wait_for(asyncio.shield(task), timeout=1)

    assert response_state["received"]

    metric_names = [metric.name for metric in metrics_before]
    assert len(metric_names) > 0, "No metrics found in Prometheus registry"
    expected_metrics = [
        "gabriel_engine_processing_latency_seconds",
        "gabriel_producer_queue_length",
        "gabriel_producer_inputs_received",
        "gabriel_engine_inputs_received",
        "gabriel_engine_inputs_processed",
    ]
    for expected_metric in expected_metrics:
        assert expected_metric in metric_names, (
            f"{expected_metric} not found in metrics"
        )

    with contextlib.suppress(TimeoutError, asyncio.TimeoutError):
        await asyncio.wait_for(task, timeout=1)
    final_metrics = list(REGISTRY.collect())

    metrics_found = 0
    for metric in final_metrics:
        metrics_found += 1
        if metric.name == "gabriel_engine_processing_latency_seconds":
            found = False
            for sample in metric.samples:
                if (
                    sample.name
                    == "gabriel_engine_processing_latency_seconds_count"
                    and sample.labels.get("engine_id") == "Engine-0"
                ):
                    found = True
                    init_val = (
                        find_value(
                            metrics_before,
                            "gabriel_engine_processing_latency_seconds_count",
                            "engine_id",
                            "Engine-0",
                        )
                        or 0
                    )
                    assert sample.value - init_val == 5
            assert found
        elif metric.name == "gabriel_producer_queue_length":
            assert len(metric.samples) == 0
        elif metric.name == "gabriel_producer_inputs_received":
            found = False
            logger.info(metric)
            for sample in metric.samples:
                if sample.name == "gabriel_producer_inputs_received_total":
                    found = True
                    assert sample.value == 5
            assert found
        elif metric.name == "gabriel_engine_inputs_received":
            found = False
            for sample in metric.samples:
                if sample.name == "gabriel_engine_inputs_received_total":
                    found = True
                    assert sample.value == 5
            assert found
        elif metric.name == "gabriel_engine_inputs_processed":
            found = False
            for sample in metric.samples:
                if sample.name == "gabriel_engine_inputs_processed_total":
                    found = True
                    assert sample.value == 5
            assert found
        else:
            metrics_found -= 1

    assert metrics_found == len(expected_metrics)


@pytest.mark.asyncio
@pytest.mark.parametrize("num_inputs_to_send", [5])
async def test_prometheus_client_metrics(
    input_producer,
    server_frontend_port,
    target_engines,
    run_engines,
    response_state,
    prometheus_client_port,
    metrics_before,
):
    """Test that Prometheus metrics are being collected at the client."""
    response_state.clear()
    response_state["received"] = False

    client = ZeroMQClient(
        f"tcp://{DEFAULT_SERVER_HOST}:{server_frontend_port}",
        input_producer,
        get_consumer(response_state),
        prometheus_client_port,
    )
    task = asyncio.create_task(client.launch_async())

    with contextlib.suppress(TimeoutError, asyncio.TimeoutError):
        await asyncio.wait_for(asyncio.shield(task), timeout=1)

    assert response_state["received"]

    metric_names = [metric.name for metric in metrics_before]
    assert len(metric_names) > 0, "No metrics found in Prometheus registry"
    expected_metrics = [
        "gabriel_producer_token_count",
        "gabriel_producer_inputs_sent",
        "gabriel_client_input_processing_latency_seconds",
    ]
    for expected_metric in expected_metrics:
        assert expected_metric in metric_names, (
            f"{expected_metric} not found in metrics"
        )

    with contextlib.suppress(TimeoutError, asyncio.TimeoutError):
        await asyncio.wait_for(task, timeout=2)
    final_metrics = list(REGISTRY.collect())

    metrics_found = 0
    for metric in final_metrics:
        metrics_found += 1
        if metric.name == "gabriel_producer_inputs_sent":
            found = False
            for sample in metric.samples:
                if sample.name == "gabriel_producer_inputs_sent_total":
                    found = True
                    assert sample.value == 5
            assert found
        elif metric.name == "gabriel_client_input_processing_latency_seconds":
            found = False
            for sample in metric.samples:
                if (
                    sample.name
                    == "gabriel_client_input_processing_latency_seconds_count"
                ):
                    found = True
                    init_val = (
                        find_value(
                            metrics_before,
                            "gabriel_client_input_processing_latency_seconds_count",
                            "producer_id",
                            input_producer[0].producer_id,
                        )
                        or 0
                    )
                    assert init_val == 0
                    assert sample.value == 5
            assert found
        elif metric.name == "gabriel_producer_token_count":
            found = False
            for sample in metric.samples:
                if sample.name == "gabriel_producer_token_count":
                    found = True
                    assert sample.value > 0
            assert found
        else:
            metrics_found -= 1
    assert metrics_found == len(expected_metrics)
