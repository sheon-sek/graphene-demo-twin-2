"""The running twin: the Live World, its projection and the frames every surface observes.

A Frame is one published Live World step: the projected points, a copy of the world state and
the Event Log at one sim second. Every surface (REST, SSE, OPC UA) reads published Frames and
never the live state directly, so they all observe the same step (the Coherent World). Every
second the Live World steps through is published, even when several are caught up at once.
"""

import asyncio
import itertools
import math
import threading
import time
from collections import OrderedDict, deque
from collections.abc import Awaitable, Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from graphene_demo_twin.asset_model import AssetModel
from graphene_demo_twin.plant_design import PlantDesign
from graphene_demo_twin.projection import Binding, Projection, Projector, placeholder_bindings
from graphene_demo_twin.sim import (
    Domain,
    Event,
    LiveWorld,
    PlaceholderHallDomain,
    WhatIfFork,
    WorldState,
)

MAX_FORKS = 8
"""What-if Forks kept at once; creating one more drops the oldest."""
FRAME_BACKLOG = 60
"""Recent frames kept for surfaces that read behind the latest; a reader further behind skips
to the oldest one kept."""


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
    events: tuple[Event, ...]
    """The Event Log when the frame was published."""

    @property
    def time(self) -> int:
        return self.projection.time

    @property
    def event_count(self) -> int:
        return len(self.events)


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
        domains: Iterable[Domain] | None = None,
        bindings: Iterable[Binding] | None = None,
        max_forks: int = MAX_FORKS,
        frame_backlog: int = FRAME_BACKLOG,
    ) -> None:
        self.asset_model = asset_model
        self.design = design
        self._clock = clock
        if domains is None:
            domains = [PlaceholderHallDomain()]
        if bindings is None:
            bindings = placeholder_bindings(asset_model, design)
        self.projector = Projector(asset_model, bindings)
        self.live = LiveWorld(design, domains, seed, clock)
        self.forks = ForkRegistry(self._fork, max_forks)
        self._lock = threading.RLock()
        self._seq = itertools.count()
        self._epoch = 0
        self._closed = False
        self._waiters: set[tuple[asyncio.AbstractEventLoop, asyncio.Future[None]]] = set()
        self._waiters_lock = threading.Lock()
        self._frames: deque[Frame] = deque(maxlen=frame_backlog)
        self._frames_lock = threading.Lock()
        self._frame = self._publish(self._project())

    @property
    def frame(self) -> Frame:
        """The latest published frame."""
        return self._frame

    def tick(self) -> Frame | None:
        """Step the Live World up to the wall clock, publishing a frame for each second; returns
        the latest new frame, or None if there was nothing to publish."""
        with self._lock:
            return self._catch_up()

    def submit(self, kind: str, target: str, params: Mapping[str, Any] | None = None) -> Event:
        """Log an Operator Command or fault action in the Live World and publish the longer Event
        Log; it takes effect on the next step. Raises EventError if no domain accepts it."""
        with self._lock:
            self._catch_up()
            event = self.live.submit(kind, target, params)
            if self._catch_up() is None:
                # No step, so the state is the published one: republish it with the new event.
                self._publish(replace(self._frame, seq=next(self._seq), events=self.live.events))
            return event

    def reset(self) -> Frame:
        """Rebuild the Live World from its initial state, drop every fork, and publish."""
        with self._lock:
            self._catch_up()
            self.live.reset()
            self.forks.clear()
            self._epoch += 1
            return self._publish(self._project())

    async def next_frame(self, after: int) -> Frame | None:
        """The first frame kept with `seq` above `after`, waiting for it if need be; None once
        the twin is closed. Read one at a time, frames never skip a second unless the reader
        falls more than the frame backlog behind."""
        loop = asyncio.get_running_loop()
        while not self._closed:
            waiter = (loop, loop.create_future())
            with self._waiters_lock:
                self._waiters.add(waiter)
            try:
                if (frame := self._first_after(after)) is not None:
                    return frame
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

    def _catch_up(self) -> Frame | None:
        """Publish every Live World step not yet published, stepping one second at a time up to
        the wall clock; returns the latest new frame, if any."""
        frame = None
        if self.live.time != self._frame.time:  # the Live World caught itself up (submit, fork)
            frame = self._publish(self._project())
        while self.live.step_if_behind():
            frame = self._publish(self._project())
        return frame

    def _fork(self) -> WhatIfFork:
        with self._lock:
            self._catch_up()
            return self.live.fork()

    def _publish(self, frame: Frame) -> Frame:
        with self._frames_lock:
            self._frames.append(frame)
            self._frame = frame
        self._wake()
        return frame

    def _first_after(self, seq: int) -> Frame | None:
        with self._frames_lock:
            # Kept frames have consecutive seqs.
            i = max(0, seq + 1 - self._frames[0].seq)
            return self._frames[i] if i < len(self._frames) else None

    def _project(self) -> Frame:
        state = self.live.state
        projection = self.projector.project(state)
        return Frame(next(self._seq), self._epoch, projection, state, self.live.events)

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
