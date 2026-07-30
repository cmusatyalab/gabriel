"""Tests for optional gRPC transport security (TLS and mutual TLS)."""

import asyncio
import contextlib
import logging
import subprocess

import pytest
from gabriel_client.grpc_client import GrpcClient
from gabriel_server.network_engine import server_runner
from gabriel_server.network_engine.server_runner import Transport
from helpers import (
    DEFAULT_NUM_TOKENS,
    INPUT_QUEUE_MAXSIZE,
    Engine,
    free_port_generator,
    get_consumer,
    wait_until,
)

logger = logging.getLogger(__name__)


@pytest.fixture(scope="session")
def tls_certs(tmp_path_factory):
    """Generate a self-signed cert/key pair for TLS tests."""
    cert_dir = tmp_path_factory.mktemp("tls_certs")
    cert_path = cert_dir / "server.crt"
    key_path = cert_dir / "server.key"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            str(key_path),
            "-out",
            str(cert_path),
            "-days",
            "1",
            "-subj",
            "/CN=localhost",
        ],
        check=True,
        capture_output=True,
    )
    return str(cert_path), str(key_path)


@pytest.mark.asyncio
async def test_grpc_tls(
    tls_certs,
    server_frontend_port,
    server_backend_port,
    prometheus_server_port,
    prometheus_client_port,
    input_producer,
    response_state,
):
    """Test that gRPC TLS works end-to-end for both the client and engine."""
    cert_path, key_path = tls_certs
    response_state.clear()
    response_state["received"] = False

    server_run = server_runner.ServerRunner(
        client_endpoint=server_frontend_port,
        engine_endpoint=server_backend_port,
        num_tokens=DEFAULT_NUM_TOKENS,
        input_queue_maxsize=INPUT_QUEUE_MAXSIZE,
        client_transport=Transport.GRPC,
        prometheus_port=prometheus_server_port,
        tls_cert=cert_path,
        tls_key=key_path,
    )
    server_task = asyncio.create_task(server_run.run_async())

    engine = Engine(
        0,
        f"localhost:{server_backend_port}",
        tls_ca_cert=cert_path,
    )
    engine_task = asyncio.create_task(engine.run_async())

    client = GrpcClient(
        f"localhost:{server_frontend_port}",
        input_producer,
        get_consumer(response_state),
        prometheus_port=prometheus_client_port,
        tls_ca_cert=cert_path,
    )
    client_task = asyncio.create_task(client.launch_async())

    await wait_until(
        lambda: response_state["received"], timeout=8, interval=0.2
    )

    try:
        assert response_state["received"]
    finally:
        for task in (client_task, engine_task, server_task):
            task.cancel()
        await asyncio.gather(
            client_task, engine_task, server_task, return_exceptions=True
        )


@pytest.mark.asyncio
async def test_grpc_mtls_rejects_untrusted_engine(
    tls_certs,
    server_backend_port,
    prometheus_server_port,
):
    """Test that mTLS keeps an engine without a client cert from registering.

    Reuses the self-signed server cert as its own CA, so it is both the
    cert the server presents and the CA the server trusts client certs
    against.
    """
    cert_path, key_path = tls_certs
    server_run = server_runner.ServerRunner(
        client_endpoint=next(free_port_generator()),
        engine_endpoint=server_backend_port,
        num_tokens=DEFAULT_NUM_TOKENS,
        input_queue_maxsize=INPUT_QUEUE_MAXSIZE,
        client_transport=Transport.GRPC,
        prometheus_port=prometheus_server_port,
        tls_cert=cert_path,
        tls_key=key_path,
        tls_client_ca_cert=cert_path,
    )
    server_task = asyncio.create_task(server_run.run_async())

    # An engine that only verifies the server (no client cert) should be
    # rejected by the server's mutual-TLS requirement, so it should never
    # successfully register.
    untrusted_engine = Engine(
        "untrusted",
        f"localhost:{server_backend_port}",
        tls_ca_cert=cert_path,
    )
    untrusted_task = asyncio.create_task(untrusted_engine.run_async())

    # Negative assertion: mTLS handshake failure is near-instant, so a fixed
    # wait is fine here (there's no "eventually succeeds" case to poll for).
    await asyncio.sleep(1)
    assert "Engine-untrusted" not in server_run.server._engine_ids

    untrusted_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await untrusted_task

    # An engine presenting a client cert signed by the trusted CA should be
    # allowed to register.
    trusted_engine = Engine(
        "trusted",
        f"localhost:{server_backend_port}",
        tls_ca_cert=cert_path,
        tls_client_cert=cert_path,
        tls_client_key=key_path,
    )
    trusted_task = asyncio.create_task(trusted_engine.run_async())

    await wait_until(
        lambda: "Engine-trusted" in server_run.server._engine_ids,
        timeout=8,
        interval=0.2,
    )

    try:
        assert "Engine-trusted" in server_run.server._engine_ids
    finally:
        for task in (trusted_task, server_task):
            task.cancel()
        await asyncio.gather(trusted_task, server_task, return_exceptions=True)
