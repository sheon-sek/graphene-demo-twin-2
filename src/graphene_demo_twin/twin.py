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
from graphene_demo_twin.event_log import EventLogDoc, EventLogError, export_log
from graphene_demo_twin.faults import (
    CLEAR,
    INJECT,
    STANDARD_CATALOG,
    FaultCatalog,
    FaultConflict,
    FaultError,
    FaultParams,
    FaultPreview,
    fault_active,
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
        catalog: FaultCatalog = STANDARD_CATALOG,
        domains: Iterable[Domain] | None = None,
        bindings: Iterable[Binding] | None = None,
        max_forks: int = MAX_FORKS,
        frame_backlog: int = FRAME_BACKLOG,
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
        Log; it takes effect on the next step. Fault actions and Operator Commands are checked
        as `inject_fault`, `clear_fault` and `command` check them. Raises EventError if no
        domain accepts it."""
        params = dict(params or {})
        if kind == INJECT:
            return self.inject_fault(target, params.get("fault"), params)
        if kind == CLEAR:
            _only(params, "fault")
            return self.clear_fault(target, params.get("fault"))
        if kind == COMMAND:
            _only(params, "command", "value")
            return self.command(target, params.get("command"), params.get("value"))
        return self._log(kind, target, params)

    def inject_fault(self, target: str, fault: Any, params: Mapping[str, Any]) -> Event:
        """Log a fault injection on exactly `target`. Raises FaultError if the fault does not
        apply to that asset or its parameters are bad, FaultConflict if it is already active
        or already logged to start."""
        normal = {"fault": fault, **FaultParams.parse(params).as_params()}
        with self._lock:
            self._check_fault(INJECT, target, normal)
            self._catch_up()
            if self._fault_live(target, fault):
                raise FaultConflict(f"{fault} is already active on {target}")
            return self._log(INJECT, target, normal)

    def clear_fault(self, target: str, fault: Any) -> Event:
        """Log a Clear of one active fault; raises FaultConflict if it is not active."""
        with self._lock:
            self._check_fault(CLEAR, target, {"fault": fault})
            self._catch_up()
            if not self._fault_live(target, fault):
                raise FaultConflict(f"{fault} is not active on {target}")
            return self._log(CLEAR, target, {"fault": fault})

    def preview_fault(
        self, target: str, fault: str, params: Mapping[str, Any], seconds: int
    ) -> FaultPreview:
        """Fault Preview from the Live World as last published; the Live World is untouched.
        Rejected as injecting the fault now would be (FaultError, FaultConflict)."""
        return preview_fault(
            self._fork(),
            self.projector,
            self.asset_model,
            target,
            fault,
            FaultParams.parse(params),
            seconds,
            self.catalog,
        )

    def command(self, target: str, command: Any, value: Any) -> Event:
        """Log an Operator Command on `target`; raises EventError if it cannot take it."""
        placed = self.design.assets.get(target)
        specs = self.commands.get(placed.type_id, ()) if placed else ()
        if not specs:
            raise EventError(f"{target} takes no Operator Commands")
        params = {"command": command, "value": value}
        if (reason := command_problem(specs, params)) is not None:
            raise EventError(reason)
        return self._log(COMMAND, target, params)

    def play(self, doc: EventLogDoc, *, reset: bool = True) -> tuple[int, list[Event]]:
        """Play a pre-authored or imported Event Log against the Live World: each action is
        logged at its offset from now. With `reset` the world is first rebuilt from its
        initial state, so the story starts from steady state. Every action is checked, and
        the story as a whole must hold together (no fault injected twice or cleared while
        not active), before any is logged; raises EventLogError otherwise. Returns the sim
        time the story starts at and the events logged."""
        with self._lock:
            if reset:
                self.reset()
            self._catch_up()
            start = self.live.time
            events = [e.event(start) for e in doc.entries]
            self._check_story(events)
            try:
                self.live.fork(catch_up=False).schedule_all(events)
            except EventError as e:
                raise EventLogError(str(e)) from None
            logged = self.live.schedule(events)
            if self._catch_up() is None:
                self._publish(replace(self._frame, seq=next(self._seq), events=self.live.events))
            return start, logged

    def export(self) -> EventLogDoc:
        """The Live World's Event Log as a document, offsets from its last (re)build."""
        with self._lock:
            return export_log(self._frame.events, seed=self.live.seed, start=self.live.start_time)

    def _check_story(self, events: list[Event]) -> None:
        active: dict[tuple[str, str], int | None] = {}
        """(target, fault) → when it clears itself, None if not before a Clear."""
        seen: set[tuple[str, str]] = set()
        for i, e in enumerate(events):
            where = f"event {i} ({e.kind} on {e.target})"
            for key, until in list(active.items()):
                if until is not None and e.at >= until:
                    del active[key]
            if e.kind in (INJECT, CLEAR):
                if (reason := self.catalog.check(e, self.design)) is not None:
                    raise EventLogError(f"{where}: {reason}")
                key = (e.target, e.params["fault"])
                if key not in seen:  # its state before the story: as the Live World has it
                    seen.add(key)
                    if self._fault_live(*key):
                        active[key] = None
                if e.kind == INJECT:
                    if key in active:
                        raise EventLogError(f"{where}: {key[1]} is already active")
                    clears = FaultParams.parse(e.params).auto_clear_s
                    active[key] = None if clears is None else e.at + clears
                else:
                    if key not in active:
                        raise EventLogError(f"{where}: {key[1]} is not active")
                    del active[key]
            elif e.kind == COMMAND:
                placed = self.design.assets.get(e.target)
                specs = self.commands.get(placed.type_id, ()) if placed else ()
                if not specs:
                    raise EventLogError(f"{where}: {e.target} takes no Operator Commands")
                if (reason := command_problem(specs, e.params)) is not None:
                    raise EventLogError(f"{where}: {reason}")

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

    def _check_fault(self, kind: str, target: str, params: Mapping[str, Any]) -> None:
        if (reason := self.catalog.check(Event(0, kind, target, params), self.design)) is not None:
            raise FaultError(reason)

    def _fault_live(self, target: str, fault: str) -> bool:
        """Whether the fault is active in the Live World after every logged event so far,
        including those that take effect on the next step."""
        return fault_active(self.live.state, self.live.events, target, fault)

    def _log(self, kind: str, target: str, params: Mapping[str, Any]) -> Event:
        """Log an event already checked, and publish the longer Event Log."""
        with self._lock:
            self._catch_up()
            event = self.live.submit(kind, target, params)
            if self._catch_up() is None:
                # No step, so the state is the published one: republish it with the new event.
                self._publish(replace(self._frame, seq=next(self._seq), events=self.live.events))
            return event

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
        """A fork of the step just published: the wall clock may have moved on since the
        catch-up, and the fork must not step past what the surfaces were shown."""
        with self._lock:
            self._catch_up()
            return self.live.fork(catch_up=False)

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
        previous = getattr(self, "_frame", None)
        hold = previous.projection if previous is not None else None
        projection = self.projector.project(state, hold=hold)
        return Frame(next(self._seq), self._epoch, projection, state, self.live.events)

    def _wake(self) -> None:
        with self._waiters_lock:
            waiters = list(self._waiters)
        for loop, future in waiters:
            try:
                loop.call_soon_threadsafe(_resolve, future)
            except RuntimeError:  # that waiter's loop has closed
                pass


def _only(params: Mapping[str, Any], *keys: str) -> None:
    if unknown := sorted(params.keys() - set(keys)):
        raise EventError(f"unknown parameters: {unknown}")


def _resolve(future: asyncio.Future[None]) -> None:
    if not future.done():
        future.set_result(None)
