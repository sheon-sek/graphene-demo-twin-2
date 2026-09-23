"""The Live World (ADR-0003): the one simulation locked to wall-clock time at 1x."""

import asyncio
import math
import threading
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping
from typing import Any

from graphene_demo_twin.plant_design import PlantDesign
from graphene_demo_twin.sim.engine import Domain, Simulation, WhatIfFork
from graphene_demo_twin.sim.events import Event
from graphene_demo_twin.sim.state import WorldState


class LiveWorld:
    """The only world exposed over OPC UA. It is never paused, accelerated or rewound: its sim
    time is always the current wall-clock second, and operator actions are stamped with it.

    Concurrent operators serialize through one lock into one Event Log.
    """

    def __init__(
        self,
        design: PlantDesign,
        domains: Iterable[Domain],
        seed: int,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._design = design
        self._domains = tuple(domains)
        self._seed = seed
        self._clock = clock
        self._lock = threading.RLock()
        self._sim = self._fresh()

    @property
    def seed(self) -> int:
        return self._seed

    @property
    def time(self) -> int:
        return self._sim.time

    @property
    def state(self) -> WorldState:
        """The current state; read it, never write it (fork to try things)."""
        return self._sim.state

    @property
    def events(self) -> tuple[Event, ...]:
        return self._sim.events

    def state_hash(self) -> str:
        with self._lock:
            return self._sim.state_hash()

    def catch_up(self) -> int:
        """Step until sim time reaches the current wall-clock second; returns the steps taken."""
        with self._lock:
            target = math.floor(self._clock())
            steps = max(0, target - self._sim.time)
            self._sim.advance(steps)
            return steps

    def submit(self, kind: str, target: str, params: Mapping[str, Any] | None = None) -> Event:
        """Log an operator action at the current sim time; it takes effect on the next step."""
        with self._lock:
            self.catch_up()
            return self._sim.schedule(Event(self._sim.time, kind, target, params or {}))

    def reset(self) -> None:
        """Discard the Event Log and rebuild from the initial state, as a restart would."""
        with self._lock:
            self._sim = self._fresh()

    def fork(self) -> WhatIfFork:
        """A What-if Fork of the world as it is now; nothing done to it reaches the Live World."""
        with self._lock:
            self.catch_up()
            return self._sim.fork()

    async def run(
        self,
        stop: asyncio.Event,
        on_tick: Callable[["LiveWorld"], None] | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        """Step on every wall-clock second boundary until `stop` is set, calling `on_tick`
        after each second that advanced the world (the 1 Hz publish point)."""
        while not stop.is_set():
            now = self._clock()
            await sleep(math.floor(now) + 1 - now)
            if self.catch_up() and on_tick is not None:
                on_tick(self)

    def _fresh(self) -> Simulation:
        return Simulation(self._design, self._domains, self._seed, math.floor(self._clock()))
