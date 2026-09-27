"""The CLI and HTTP contract (docs/cli.md) agree with the code, and `serve` serves."""

import os
import re
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from fastapi.routing import APIRoute

from graphene_demo_twin.cli import build_parser
from graphene_demo_twin.surfaces.api import create_app
from graphene_demo_twin.surfaces.opcua import ENDPOINT_PATH, NAMESPACE

REPO = Path(__file__).resolve().parents[1]
DOC = (REPO / "docs" / "cli.md").read_text(encoding="utf-8")


def _table(heading: str) -> list[list[str]]:
    """Rows of the first Markdown table under `heading`, with backticks stripped."""
    section = DOC.split(f"## {heading}\n", 1)[1].split("\n## ", 1)[0]
    rows = [line for line in section.splitlines() if line.startswith("|")][2:]
    return [[c.strip().strip("`") for c in row.strip("|").split("|")] for row in rows]


def _serve_parser():
    parser = build_parser()
    commands = next(a for a in parser._actions if a.dest == "command")
    return commands.choices


def test_serve_is_the_only_command():
    assert list(_serve_parser()) == ["serve"]
    assert "`graphene-twin serve`" in DOC
    with pytest.raises(SystemExit):
        build_parser().parse_args(["opc"])


def test_documented_serve_options_match_the_parser():
    serve = _serve_parser()["serve"]
    actual = {}
    for action in serve._actions:
        if action.option_strings and action.dest != "help":
            default = action.default
            if isinstance(default, Path):
                default = default.relative_to(REPO).as_posix()
            actual[action.option_strings[0]] = str(default)
    documented = {row[0]: row[1] for row in _table("Options")}
    assert documented == actual


def test_serve_parses_overrides():
    args = build_parser().parse_args(
        ["serve", "--host", "0.0.0.0", "--http-port", "9000", "--opc-port", "4841", "--seed", "3"]
    )
    assert (args.host, args.http_port, args.opc_port, args.seed) == ("0.0.0.0", 9000, 4841, 3)


def test_documented_opc_ua_contract_matches_the_code():
    assert NAMESPACE in DOC
    assert f"opc.tcp://<host>:<opc-port>{ENDPOINT_PATH}" in DOC
    assert "point:<encodedExportPath>" in DOC


def test_documented_http_routes_match_the_app(asset_model, plant_design, tmp_path):
    from graphene_demo_twin.twin import Twin

    app = create_app(Twin(asset_model, plant_design), console_dir=tmp_path)
    actual = {
        (method, re.sub(r"\{(\w+):\w+\}", r"{\1}", route.path))  # {path:path} is {path}
        for route in app.routes
        if isinstance(route, APIRoute)
        for method in route.methods
    }
    documented = {(row[0], row[1]) for row in _table("HTTP")}
    assert documented == actual


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.slow
def test_serve_starts_api_console_and_opc_ua_together_and_stops_on_sigint(tmp_path):
    from asyncua.sync import Client

    (tmp_path / "index.html").write_text("<!doctype html><title>Console</title>")
    http_port, opc_port = _free_port(), _free_port()
    process = subprocess.Popen(
        [
            sys.executable, "-m", "graphene_demo_twin", "serve",
            "--http-port", str(http_port), "--opc-port", str(opc_port),
            "--seed", "5", "--console-dir", str(tmp_path),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )  # fmt: skip
    try:
        base = f"http://127.0.0.1:{http_port}"
        deadline = time.monotonic() + 30
        while True:
            try:
                status = httpx.get(f"{base}/api/status", timeout=1).json()
                break
            except httpx.HTTPError:
                assert process.poll() is None, process.stdout.read().decode()
                assert time.monotonic() < deadline, "serve did not come up"
                time.sleep(0.2)
        assert status["seed"] == 5
        assert abs(status["time"] - time.time()) < 5  # the Live World runs on the wall clock
        assert "<title>Console</title>" in httpx.get(base).text

        client = Client(f"opc.tcp://127.0.0.1:{opc_port}{ENDPOINT_PATH}")
        client.connect()
        try:
            idx = client.get_namespace_index(NAMESPACE)
            node = client.get_node(f"ns={idx};s=point:Dashboard/Total IT Load")
            assert node.read_value() > 0
        finally:
            client.disconnect()

        process.send_signal(signal.SIGINT)
        assert process.wait(timeout=15) == 0
        out = process.stdout.read().decode()
        assert f"http://127.0.0.1:{http_port}" in out and f":{opc_port}{ENDPOINT_PATH}" in out
        assert "Traceback" not in out
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


def test_serve_reports_a_port_in_use(tmp_path):
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen()
        port = taken.getsockname()[1]
        result = subprocess.run(
            [
                sys.executable, "-m", "graphene_demo_twin", "serve",
                "--http-port", str(port), "--opc-port", str(_free_port()),
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )  # fmt: skip
    assert result.returncode == 1
    assert re.search(r"cannot serve: .*in use", result.stderr), result.stderr
