"""One process serving the REST/SSE API, the Operator Console and OPC UA from one Live World."""

import asyncio
import contextlib
import logging
import signal
import socket
import time
from collections.abc import Callable
from pathlib import Path

import uvicorn

from graphene_demo_twin.asset_model import load_asset_model
from graphene_demo_twin.plant_design import load_plant_design
from graphene_demo_twin.surfaces.api import CONSOLE_DIR, create_app
from graphene_demo_twin.surfaces.opcua import OpcUaSurface
from graphene_demo_twin.twin import Twin

log = logging.getLogger("graphene_demo_twin")


class _EmbeddedServer(uvicorn.Server):
    """uvicorn without its own signal handling: `serve` owns shutdown for every surface."""

    @contextlib.contextmanager
    def capture_signals(self):
        yield


class Services:
    """The HTTP (REST, SSE, console) and OPC UA surfaces of one twin, in the running loop."""

    def __init__(
        self,
        twin: Twin,
        *,
        host: str,
        http_port: int,
        opc_port: int,
        console_dir: Path | None = CONSOLE_DIR,
    ) -> None:
        self.twin = twin
        self.opc = OpcUaSurface(twin, host, opc_port)
        self._host = host
        self._http_port = http_port
        self._http = _EmbeddedServer(
            uvicorn.Config(
                create_app(twin, console_dir),
                log_level="warning",
                lifespan="off",
                timeout_graceful_shutdown=2,
            )
        )
        self._socket: socket.socket | None = None
        self._tasks: list[asyncio.Task] = []

    @property
    def http_port(self) -> int:
        """The bound HTTP port (resolved when started with port 0)."""
        assert self._socket is not None
        return self._socket.getsockname()[1]

    async def start(self) -> None:
        # Bind here so a port clash raises OSError instead of exiting inside uvicorn.
        self._socket = socket.create_server((self._host, self._http_port))
        try:
            await self.opc.start()
        except BaseException:
            self._socket.close()
            raise
        self._tasks = [
            asyncio.create_task(self.opc.run(), name="opcua-publish"),
            asyncio.create_task(self._http.serve(sockets=[self._socket]), name="http"),
        ]
        while not self._http.started:
            if self._tasks[1].done():
                self._tasks[1].result()
            await asyncio.sleep(0.01)

    async def stop(self) -> None:
        self.twin.close()  # ends SSE streams and the OPC UA publish loop
        self._http.should_exit = True
        await asyncio.gather(*self._tasks, return_exceptions=True)
        await self.opc.stop()
        if self._socket is not None:
            self._socket.close()


async def serve(
    *,
    host: str,
    http_port: int,
    opc_port: int,
    seed: int,
    console_dir: Path | None = CONSOLE_DIR,
    clock: Callable[[], float] = time.time,
) -> None:
    """Run until SIGINT or SIGTERM."""
    asset_model = load_asset_model()
    design = load_plant_design(asset_model)
    twin = Twin(asset_model, design, seed=seed, clock=clock)
    services = Services(
        twin, host=host, http_port=http_port, opc_port=opc_port, console_dir=console_dir
    )
    await services.start()

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    log.warning(
        "Graphene Demo Twin: API and console on http://%s:%d, OPC UA on %s (seed %d)",
        host,
        services.http_port,
        services.opc.endpoint,
        seed,
    )
    try:
        await twin.run(stop)
    finally:
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(sig)
        await services.stop()
