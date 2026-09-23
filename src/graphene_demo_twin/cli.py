"""`graphene-twin`, the command line. Its contract is documented in docs/cli.md, and a test
keeps the two in agreement."""

import argparse
import asyncio
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from graphene_demo_twin.surfaces.api import CONSOLE_DIR


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="graphene-twin",
        description="Graphene Demo Twin: a deterministic virtual datacenter.",
    )
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    serve = commands.add_parser(
        "serve",
        help="serve the REST/SSE API, the Operator Console and OPC UA from one Live World",
        description="Serve the REST/SSE API, the Operator Console and OPC UA together, "
        "from one Live World, until interrupted.",
    )
    serve.add_argument(
        "--host", default="127.0.0.1", help="interface both HTTP and OPC UA listen on"
    )
    serve.add_argument(
        "--http-port", type=int, default=8080, help="port for the REST/SSE API and console"
    )
    serve.add_argument("--opc-port", type=int, default=4840, help="port for OPC UA")
    serve.add_argument("--seed", type=int, default=0, help="seed of the Live World")
    serve.add_argument(
        "--console-dir",
        type=Path,
        default=CONSOLE_DIR,
        help="built Operator Console to serve at /",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    if args.command == "serve":
        from graphene_demo_twin.serve import serve

        try:
            asyncio.run(
                serve(
                    host=args.host,
                    http_port=args.http_port,
                    opc_port=args.opc_port,
                    seed=args.seed,
                    console_dir=args.console_dir,
                )
            )
        except OSError as e:
            print(f"graphene-twin: cannot serve: {e}", file=sys.stderr)
            return 1
    return 0
