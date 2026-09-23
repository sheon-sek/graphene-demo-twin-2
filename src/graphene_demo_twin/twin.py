"""The running twin: the Live World, its projection and the frames every surface observes.

A Frame is one published Live World step: the projected points and a copy of the world state
at one sim second. Every surface (REST, SSE, OPC UA) reads the latest Frame and never the live
state directly, so they all observe the same step (the Coherent World).
"""

import asyncio
import itertools
import math
import threading
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any

from graphene_demo_twin.asset_model import AssetModel
from graphene_demo_twin.faults import (
    CLEAR,
    INJECT,
    STANDARD_CATALOG,
    FaultCatalog,
    FaultConflict,
    FaultError,
    FaultParams,
    FaultPreview,
    fault_key,
    preview_fault,
)
from graphene_demo_twin.plant_design import PlantDesign
from graphene_demo_twin.projection import Binding, Projection, Projector
from graphene_demo_twin.sim import (
    COMMAND,
    CommandSpec,
    Domain,
    Event,
    EventError,
    LiveWorld,
    WhatIfFork,
    WorldState,
    command_problem,
)
from graphene_demo_twin.world import default_domains, default_projector

MAX_FORKS = 8
"""What-if Forks kept at once; creating one more drops the oldest."""


@dataclass(frozen=True, slots=True)
class Frame:
    """One published Live World step."""

    seq: int
    """Publish counter, increasing by one per frame across Resets."""
    epoch: int
    """Resets so far; a new epoch means the world was rebuilt from its initial state."""
    projection: Projection
    state: WorldState
    """A copy of the Live World state the projection was made from."""
    event_count: int
    """Length of the Event Log when the frame was published."""

    @property
    def time(self) -> int:
        return self.projection.time


@dataclass(slots=True)
class ForkSession:
    """A What-if Fork held for the Operator Console, with a lock serializing its callers."""

    id: str
    fork: WhatIfFork
    lock: threading.Lock = field(default_factory=threading.Lock)


class ForkRegistry:
    """The What-if Forks in use, oldest first."""

    def __init__(self, source: Callable[[], WhatIfFork], limit: int) -> None:
        self._source = source
        self._limit = limit
        self._ids = itertools.count(1)
        self._sessions: OrderedDict[str, ForkSession] = OrderedDict()
        self._lock = threading.Lock()

    def create(self) -> ForkSession:
        session = ForkSession(f"fork-{next(self._ids)}", self._source())
        with self._lock:
            self._sessions[session.id] = session
            while len(self._sessions) > self._limit:
                self._sessions.popitem(last=False)
        return session

    def get(self, fork_id: str) -> ForkSession:
        return self._sessions[fork_id]

    def delete(self, fork_id: str) -> None:
        with self._lock:
            del self._sessions[fork_id]

    def clear(self) -> None:
        with self._lock:
            self._sessions.clear()

    def __iter__(self) -> Iterator[ForkSession]:
        return iter(list(self._sessions.values()))

    def __len__(self) -> int:
        return len(self._sessions)


class Twin:
    """Owns the Live World and publishes a Frame for each second it advances.

    Safe to drive from several threads: publishing is serialized, and waiters on any event
    loop are woken thread-safely.
    """

    def __init__(
        self,
        asset_model: AssetModel,
        design: PlantDesign,
        *,
        seed: int = 0,
        clock: Callable[[], float] = time.time,
        catalog: FaultCatalog = STANDARD_CATALOG,
        domains: Iterable[Domain] | None = None,
        bindings: Iterable[Binding] | None = None,
        max_forks: int = MAX_FORKS,
    ) -> None:
        self.asset_model = asset_model
        self.design = design
        self.catalog = catalog
        self._clock = clock
        domains = default_domains(catalog) if domains is None else list(domains)
        self.projector = (
            default_projector(asset_model, design, catalog)
            if bindings is None
            else Projector(asset_model, bindings)
        )
        self.commands: dict[str, tuple[CommandSpec, ...]] = {}
        """Operator Commands by asset type, as the domains that carry them out declare."""
        for domain in domains:
            for type_id, specs in getattr(domain, "commands", {}).items():
                self.commands[type_id] = self.commands.get(type_id, ()) + tuple(specs)
        self.live = LiveWorld(design, domains, seed, clock)
        self.forks = ForkRegistry(self.live.fork, max_forks)
        self._lock = threading.RLock()
        self._seq = itertools.count()
        self._epoch = 0
        self._closed = False
        self._waiters: set[tuple[asyncio.AbstractEventLoop, asyncio.Future[None]]] = set()
        self._waiters_lock = threading.Lock()
        self._frame = self._project()

    @property
    def frame(self) -> Frame:
        """The latest published frame."""
        return self._frame

    def tick(self) -> Frame | None:
        """Step the Live World up to the wall clock and publish if it moved on since the last
        frame; returns the new frame, or None if there was nothing to publish."""
        with self._lock:
            self.live.catch_up()
            if self.live.time == self._frame.time:
                return None
            return self._publish()

    def submit(self, kind: str, target: str, params: Mapping[str, Any] | None = None) -> Event:
        """Log an Operator Command or fault action in the Live World; it takes effect on the
        next step. Raises EventError if no domain accepts it."""
        with self._lock:
            return self.live.submit(kind, target, params)

    def inject_fault(self, target: str, fault: str, params: Mapping[str, Any]) -> Event:
        """Log a fault injection on exactly `target`. Raises FaultError if the fault does not
        apply to that asset or its parameters are bad, FaultConflict if it is already active
        or already logged to start."""
        normal = {"fault": fault, **FaultParams.parse(params).as_params()}
        with self._lock:
            self._check_fault(INJECT, target, normal)
            if self._fault_live(target, fault):
                raise FaultConflict(f"{fault} is already active on {target}")
            return self.live.submit(INJECT, target, normal)

    def clear_fault(self, target: str, fault: str) -> Event:
        """Log a Clear of one active fault; raises FaultConflict if it is not active."""
        with self._lock:
            self._check_fault(CLEAR, target, {"fault": fault})
            if not self._fault_live(target, fault):
                raise FaultConflict(f"{fault} is not active on {target}")
            return self.live.submit(CLEAR, target, {"fault": fault})

    def preview_fault(
        self, target: str, fault: str, params: Mapping[str, Any], seconds: int
    ) -> FaultPreview:
        """Fault Preview from the Live World as it is now; the Live World is untouched."""
        return preview_fault(
            self.live.fork(),
            self.projector,
            self.asset_model,
            target,
            fault,
            FaultParams.parse(params),
            seconds,
            self.catalog,
        )

    def command(self, target: str, command: str, value: Any) -> Event:
        """Log an Operator Command on `target`; raises EventError if it cannot take it."""
        placed = self.design.assets.get(target)
        specs = self.commands.get(placed.type_id, ()) if placed else ()
        if not specs:
            raise EventError(f"{target} takes no Operator Commands")
        params = {"command": command, "value": value}
        if (reason := command_problem(specs, params)) is not None:
            raise EventError(reason)
        return self.submit(COMMAND, target, params)

    def reset(self) -> Frame:
        """Rebuild the Live World from its initial state, drop every fork, and publish."""
        with self._lock:
            self.live.reset()
            self.forks.clear()
            self._epoch += 1
            return self._publish()

    async def next_frame(self, after: int) -> Frame | None:
        """The first frame with `seq` above `after`, waiting for it if need be; None once the
        twin is closed."""
        loop = asyncio.get_running_loop()
        while not self._closed:
            waiter = (loop, loop.create_future())
            with self._waiters_lock:
                self._waiters.add(waiter)
            try:
                if self._frame.seq > after:
                    return self._frame
                if self._closed:
                    break
                await waiter[1]
            finally:
                with self._waiters_lock:
                    self._waiters.discard(waiter)
        return None

    def close(self) -> None:
        """Release every waiter; later calls to `next_frame` return None."""
        self._closed = True
        self._wake()

    async def run(
        self,
        stop: asyncio.Event,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        """Tick on every wall-clock second boundary until `stop` is set."""
        while not stop.is_set():
            now = self._clock()
            await sleep(math.floor(now) + 1 - now)
            self.tick()

    def _check_fault(self, kind: str, target: str, params: Mapping[str, Any]) -> None:
        if (reason := self.catalog.check(Event(0, kind, target, params), self.design)) is not None:
            raise FaultError(reason)

    def _fault_live(self, target: str, fault: str) -> bool:
        """Whether the fault is active in the Live World after every logged event so far,
        including those that take effect on the next step."""
        self.live.catch_up()
        active = fault_key(target, fault) in self.live.state.faults
        for event in self.live.events:
            if event.at >= self.live.time and event.target == target:
                if event.params.get("fault") == fault:
                    active = event.kind == INJECT
        return active

    def _publish(self) -> Frame:
        self._frame = self._project()
        self._wake()
        return self._frame

    def _project(self) -> Frame:
        state = self.live.state.copy()
        projection = self.projector.project(state)
        return Frame(next(self._seq), self._epoch, projection, state, len(self.live.events))

    def _wake(self) -> None:
        with self._waiters_lock:
            waiters = list(self._waiters)
        for loop, future in waiters:
            try:
                loop.call_soon_threadsafe(_resolve, future)
            except RuntimeError:  # that waiter's loop has closed
                pass


def _resolve(future: asyncio.Future[None]) -> None:
    if not future.done():
        future.set_result(None)
