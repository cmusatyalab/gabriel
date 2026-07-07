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
DEFAULT_LOG_LEVEL = "DEBUG"
INPUT_QUEUE_MAXSIZE = 60

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
        default=INPUT_QUEUE_MAXSIZE,
        help="Max input queue size",
    )

    parser.add_argument(
        "--transport",
        choices=[transport.value for transport in Transport],
        default=Transport.ZEROMQ.value,
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

    args, _ = parser.parse_known_args()

    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s - %(filename)s:%(lineno)d - %(levelname)s - "
        "%(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    print(args.client_port)
    if args.client_port and args.client_path:
        raise ValueError("Can't specify both port and path")

    use_ipc = False
    if args.client_path:
        use_ipc = True

    client_endpoint = (
        args.client_port if not args.client_path else args.client_path
    )

    server_runner = ServerRunner(
        client_endpoint=client_endpoint,
        engine_zmq_endpoint=f"tcp://*:{args.engine_port}",
        num_tokens=args.tokens,
        input_queue_maxsize=INPUT_QUEUE_MAXSIZE,  # TODO: Don't hardcode this
        transport=Transport(args.transport),
        use_ipc=use_ipc,
    )
    server_runner.run()


if __name__ == "__main__":
    main()
