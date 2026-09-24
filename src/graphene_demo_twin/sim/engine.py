"""The stepping engine (ADR-0003): fixed 1 s steps from a steady-state initial state."""

import bisect
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Protocol, Self

from graphene_demo_twin.plant_design import PlantDesign
from graphene_demo_twin.sim.events import Event, EventError
from graphene_demo_twin.sim.noise import Noise
from graphene_demo_twin.sim.state import AssetState, WorldState, state_hash

STEP_S = 1.0
"""The fixed step, in seconds."""
COMPLETION_PASSES = 20
"""Most passes of the domains' `complete` for the initial state to settle; a coupled
steady state converges in a handful."""


@dataclass(frozen=True, slots=True)
class StepContext:
    """What a domain may read besides the world state while it steps out of `time`."""

    design: PlantDesign
    noise: Noise
    time: int
    dt: float = STEP_S

    def uniform(self, key: str) -> float:
        return self.noise.uniform(key, self.time)

    def gauss(self, key: str) -> float:
        return self.noise.gauss(key, self.time)


class Domain(Protocol):
    """A domain model plugged into the engine as a step function over AssetState.

    Domains hold no mutable state of their own: everything that evolves lives in WorldState,
    so forks, replay and Reset see all of it. They step in registration order, each reading
    what earlier domains wrote in the same step. A domain that carries out Operator Commands
    declares them as `commands: Mapping[type_id, tuple[CommandSpec, ...]]`. A domain whose
    steady state derives from other domains' (site totals, running averages) may also define
    `complete(state, ctx)`, called in registration order once every domain's initial state is
    in place, and then again in the same order until a pass changes nothing, so a steady state
    may also derive from domains registered after it and settle with the ones it is coupled
    to (a room's heat balance from the losses the electrical network works out, and a CRAC
    unit's load from the room it cools). It sets only its own variables, from the state as it
    finds it.
    """

    def initial(self, ctx: StepContext) -> Mapping[str, AssetState]:
        """The steady-state AssetState variables this domain owns, at the start time."""
        ...

    def handles(self, event: Event, design: PlantDesign) -> bool:
        """Whether this domain gives `event` a meaning; checked when it is scheduled."""
        ...

    def apply(self, event: Event, state: WorldState) -> None:
        """Take `event` into the state, before the step out of `event.at`."""
        ...

    def step(self, state: WorldState, ctx: StepContext) -> None:
        """Advance this domain's variables in place from `ctx.time` by `ctx.dt`."""
        ...


class Simulation:
    """A world trajectory fully determined by design, domains, seed, start time and Event Log.

    It advances only when asked; the Live World drives one against wall-clock time and a
    What-if Fork is one copied from it.
    """

    def __init__(
        self, design: PlantDesign, domains: Iterable[Domain], seed: int, start_time: int
    ) -> None:
        self.design = design
        self.domains: tuple[Domain, ...] = tuple(domains)
        self.seed = seed
        self.noise = Noise(seed)
        self.start_time = start_time
        """Sim time of the initial state: what an exported Event Log's offsets count from."""
        self.state = self._initial_state(start_time)
        self._events: list[Event] = []
        self._pending = 0
        """Index of the first event not yet applied: every earlier one has `at < time`."""

    @property
    def time(self) -> int:
        return self.state.time

    @property
    def events(self) -> tuple[Event, ...]:
        """The Event Log, applied and pending, ordered by sim time then by logging order."""
        return tuple(self._events)

    def schedule(self, event: Event) -> Event:
        """Add `event` to the Event Log. It may not be earlier than the current sim time."""
        if event.at < self.time:
            raise EventError(f"event at {event.at} is in the past (sim time is {self.time})")
        self._domain_for(event)
        self._events.insert(bisect.bisect_right(self._events, event.at, key=_at), event)
        return event

    def step(self) -> None:
        t = self.state.time
        while self._pending < len(self._events) and self._events[self._pending].at == t:
            event = self._events[self._pending]
            self._domain_for(event).apply(event, self.state)
            self._pending += 1
        ctx = StepContext(self.design, self.noise, t)
        for domain in self.domains:
            domain.step(self.state, ctx)
        self.state.time = t + 1

    def advance(self, steps: int) -> None:
        for _ in range(steps):
            self.step()

    def run_until(self, time: int) -> None:
        self.advance(time - self.time)

    def state_hash(self) -> str:
        return state_hash(self.state)

    def fork(self) -> "WhatIfFork":
        return WhatIfFork._copy_of(self)

    @classmethod
    def _copy_of(cls, other: "Simulation") -> Self:
        sim = cls.__new__(cls)
        sim.design = other.design  # read-only, shared
        sim.domains = other.domains  # stateless, shared
        sim.seed = other.seed
        sim.noise = other.noise
        sim.start_time = other.start_time
        sim.state = other.state.copy()
        sim._events = list(other._events)  # events are immutable
        sim._pending = other._pending
        return sim

    def _initial_state(self, start_time: int) -> WorldState:
        ctx = StepContext(self.design, self.noise, start_time)
        assets: dict[str, AssetState] = {}
        for domain in self.domains:
            for node, variables in domain.initial(ctx).items():
                owned = assets.setdefault(node, {})
                if clash := owned.keys() & variables.keys():
                    raise ValueError(f"two domains own {node} variables {sorted(clash)}")
                owned.update(variables)
        state = WorldState(start_time, assets)
        completions = [c for d in self.domains if (c := getattr(d, "complete", None)) is not None]
        settled = None
        for _ in range(COMPLETION_PASSES):
            for complete in completions:
                complete(state, ctx)
            if settled == (settled := state_hash(state)):
                break
        return state

    def _domain_for(self, event: Event) -> Domain:
        for domain in self.domains:
            if domain.handles(event, self.design):
                return domain
        raise EventError(f"no domain handles {event.kind} on {event.target}: {dict(event.params)}")


class WhatIfFork(Simulation):
    """A copy of the Live World advanced independently: paused between calls, stepped or run
    as fast as it goes, and given hypothetical events. Only the Operator Console observes it.
    """

    forked_at: int
    """Sim time of the Live World when the fork was taken."""

    @classmethod
    def _copy_of(cls, other: Simulation) -> Self:
        fork = super()._copy_of(other)
        fork.forked_at = other.time
        return fork

    def run_for(self, seconds: int) -> None:
        self.advance(seconds)

    def schedule_all(self, events: list[Event]) -> list[Event]:
        """Schedule every event, or raise EventError at the first one no domain takes."""
        return [self.schedule(e) for e in events]


def _at(event: Event) -> int:
    return event.at
