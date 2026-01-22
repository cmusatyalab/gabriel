#!/usr/bin/env python3

# Copyright (C) 2026 Carnegie Mellon University
# SPDX-FileCopyrightText: 2023 Carnegie Mellon University - Satyalab
#
# SPDX-License-Identifier: GPL-2.0-only

"""Entrypoint for Gabriel Docker image."""

import argparse
import logging

from gabriel_server.network_engine.server_runner import ServerRunner

DEFAULT_NUM_TOKENS = 2
INPUT_QUEUE_MAXSIZE = 60

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(filename)s:%(lineno)d - %(levelname)s - "
    "%(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

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

    parser.add_argument("-p", "--port", type=int, help="Set port number")

    parser.add_argument("--path", type=str, help="Set ipc path")

    parser.add_argument(
        "-q--queue",
        type=int,
        default=INPUT_QUEUE_MAXSIZE,
        help="Max input queue size",
    )

    parser.add_argument(
        "-w",
        "--websockets",
        action="store_true",
        help="Use Websockets Gabriel client",
    )

    args, _ = parser.parse_known_args()

    print(args.port)
    if args.port and args.path:
        raise ValueError("Can't specify both port and path")

    use_ipc = False
    if args.path:
        use_ipc = True

    client_endpoint = args.port if not args.path else args.path

    server_runner = ServerRunner(
        client_endpoint=client_endpoint,
        engine_zmq_endpoint="tcp://*:5555",
        num_tokens=args.tokens,
        input_queue_maxsize=INPUT_QUEUE_MAXSIZE,  # TODO: Don't hardcode this
        use_zeromq=not args.websockets,
        use_ipc=use_ipc,
    )
    server_runner.run()


if __name__ == "__main__":
    main()
