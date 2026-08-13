#!/usr/bin/env python3

# Copyright (C) 2026 Carnegie Mellon University
# SPDX-FileCopyrightText: 2023 Carnegie Mellon University - Satyalab
#
# SPDX-License-Identifier: GPL-2.0-only

"""Entrypoint for Gabriel Docker image."""

import argparse
import logging

from gabriel_server.network_engine.server_runner import ServerRunner, Transport

DEFAULT_PORT = 9099
DEFAULT_NUM_TOKENS = 2
DEFAULT_LOG_LEVEL = "INFO"
INPUT_QUEUE_MAXLEN = 60

logger = logging.getLogger(__name__)


def main():
    """Main method for Gabriel Docker image."""
    parser = argparse.ArgumentParser(
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument(
        "-t",
        "--tokens",
        type=int,
        default=DEFAULT_NUM_TOKENS,
        help="number of tokens",
    )

    parser.add_argument(
        "-p",
        "--client_port",
        type=int,
        default=DEFAULT_PORT,
        help="Port to listen on for client connections",
    )

    parser.add_argument(
        "--client_path", type=str, help="Set client connection ipc path"
    )

    parser.add_argument(
        "-q",
        "--queue",
        type=int,
        default=INPUT_QUEUE_MAXLEN,
        help="Max input queue length",
    )

    parser.add_argument(
        "--transport",
        choices=[transport.value for transport in Transport],
        default=Transport.GRPC.value,
        help="Transport to use for client connections",
    )

    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        default=DEFAULT_LOG_LEVEL,
        help="Logging verbosity",
    )

    parser.add_argument(
        "--engine_port",
        type=int,
        default=5555,
        help="Port to listen on for engine connections",
    )

    parser.add_argument(
        "--engine_path", type=str, help="Set engine connection ipc path"
    )

    parser.add_argument(
        "--tls-cert",
        type=str,
        help="Path to a PEM certificate chain for gRPC servers to present. "
        "If omitted (along with --tls-key), gRPC servers listen on "
        "insecure (plaintext) ports.",
    )

    parser.add_argument(
        "--tls-key",
        type=str,
        help="Path to the PEM private key matching --tls-cert.",
    )

    parser.add_argument(
        "--tls-client-ca-cert",
        type=str,
        help="Path to a PEM CA certificate used to verify client/engine "
        "certificates. Providing this enables mutual TLS.",
    )

    parser.add_argument(
        "--prometheus_port",
        type=int,
        default=8000,
        help="Port for Prometheus metrics",
    )

    parser.add_argument(
        "--http2-stream-window-bytes",
        type=int,
        default=None,
        help="Override the HTTP/2 per-stream flow-control window (bytes) "
        "for client connections. Only applies to the gRPC transport. "
        "Defaults to gRPC's own default (64KiB) if not given.",
    )

    args, _ = parser.parse_known_args()

    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s - %(filename)s:%(lineno)d - %(levelname)s - "
        "%(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    if args.client_port and args.client_path:
        raise ValueError("Can't specify both port and path")

    use_client_ipc = False
    if args.client_path:
        use_client_ipc = True

    client_endpoint = (
        args.client_port if not args.client_path else args.client_path
    )

    if args.engine_port and args.engine_path:
        raise ValueError("Can't specify both port and path")

    use_engine_ipc = False
    if args.engine_path:
        use_engine_ipc = True

    engine_endpoint = (
        args.engine_port if not args.engine_path else args.engine_path
    )

    server_runner = ServerRunner(
        client_endpoint=client_endpoint,
        engine_endpoint=engine_endpoint,
        num_tokens=args.tokens,
        input_queue_maxsize=args.queue,
        client_transport=Transport(args.transport),
        use_client_ipc=use_client_ipc,
        use_engine_ipc=use_engine_ipc,
        prometheus_port=args.prometheus_port,
        tls_cert=args.tls_cert,
        tls_key=args.tls_key,
        tls_client_ca_cert=args.tls_client_ca_cert,
        http2_stream_window_bytes=args.http2_stream_window_bytes,
    )
    server_runner.run()


if __name__ == "__main__":
    main()
