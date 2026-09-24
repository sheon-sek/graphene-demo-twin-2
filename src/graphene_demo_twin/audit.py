"""The zero-fallback audit: every production point has a causal source.

A production point is any Asset Model point outside a Support Asset that is not static
metadata. The audit finds each production point that is a Causal Dead End:

- a **Compatibility Fallback**: nothing in the world drives it;
- a **constant**: it is bound, but its value is computed without reading the world state;
- a **Setpoint Mirror**: a measured, computed or alarm point that reads only setpoints.

It finds constants by evaluating every binding once against a probe of a real world state
that records which AssetState variables the binding reads. A binding that reads none
cannot respond to any change in the world. The check is structural. It looks at one
execution path, so a binding that reads the state and then ignores it would pass. Tests
that follow each fault's causal chain cover that case.

A group binding drives many points from one read, so the variables it reads say nothing
about any one of its points. The audit therefore perturbs each variable the group reads,
one at a time, and records which of the group's points change: a point no perturbation
moves is a constant, and a measured point only setpoints move is a Setpoint Mirror.

No production point is exempt.
"""

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from graphene_demo_twin.asset_model import AssetModel, SourceClass
from graphene_demo_twin.plant_design import PlantDesign
from graphene_demo_twin.projection import PointSource, Projector
from graphene_demo_twin.sim import WorldState


class DeadEnd(StrEnum):
    """Why a production point cannot respond to the world."""

    FALLBACK = "compatibility_fallback"
    CONSTANT = "constant"
    SETPOINT_MIRROR = "setpoint_mirror"


SETPOINT_VARIABLE = re.compile(r"(^|[._])(sp|setpoint)([._]|$)|_sp_|setpoint")
"""AssetState variables that hold a setpoint rather than a physical quantity."""
MEASURED = frozenset(
    {
        SourceClass.PROCESS_VALUE,
        SourceClass.FEEDBACK,
        SourceClass.FAULT_ALARM,
        SourceClass.ENERGY_INTEGRAL,
    }
)
"""Source classes whose value must come from physics, never a setpoint alone."""
UNMATCHED = "\x00unmatched"
"""A text value no reader expects: it falls through every comparison, so the reader shows
each value it would compare a text variable against."""


@dataclass(frozen=True, slots=True)
class AuditReport:
    production: int
    """Production points checked."""
    dead_ends: Mapping[str, DeadEnd]
    """Every production point that is a Causal Dead End, and why."""

    @property
    def ok(self) -> bool:
        return not self.dead_ends

    def counts(self) -> dict[str, int]:
        counts = {d.value: 0 for d in DeadEnd}
        for d in self.dead_ends.values():
            counts[d.value] += 1
        return counts

    def summary(self) -> str:
        lines = [
            f"production points: {self.production}",
            f"causal dead ends: {len(self.dead_ends)} {self.counts()}",
        ]
        lines += [f"  {d.value}: {p}" for p, d in sorted(self.dead_ends.items())]
        return "\n".join(lines)


def audit(
    asset_model: AssetModel,
    design: PlantDesign,
    projector: Projector,
    states: WorldState | Sequence[WorldState],
) -> AuditReport:
    """Audit every production point of `projector` against `states`, typically the steady
    initial state of the world and a stressed one. A point is a constant only if it reads
    nothing (a group's point: moves under no perturbation) in every state."""
    states = [states] if isinstance(states, WorldState) else list(states)
    coverage = projector.coverage
    production = {
        path
        for path, e in coverage.entries.items()
        if not e.support and e.source_class is not SourceClass.STATIC_METADATA
    }
    dead: dict[str, DeadEnd] = {}
    seen: set[tuple[str, str]] = set()
    for path in production:
        if coverage.entries[path].source is PointSource.FALLBACK:
            dead[path] = DeadEnd.FALLBACK
    for paths, read in projector.readers():
        mine = [p for p in paths if p in production]
        if not mine:
            continue
        moved_by: dict[str, set[tuple[str, str]]] = {p: set() for p in paths}
        for state in states:
            seen.clear()
            read(_Probe(state, seen))
            if len(paths) == 1:
                moved_by[paths[0]] |= seen
            else:
                for path, moved in _moved_by(state, paths, read, set(seen)).items():
                    moved_by[path] |= moved
        for path in mine:
            if not moved_by[path]:
                dead[path] = DeadEnd.CONSTANT
            elif asset_model.point(path).source_class in MEASURED and all(
                SETPOINT_VARIABLE.search(key) for _, key in moved_by[path]
            ):
                dead[path] = DeadEnd.SETPOINT_MIRROR
    return AuditReport(len(production), MappingProxyType(dict(sorted(dead.items()))))


def _moved_by(
    state: WorldState,
    paths: Sequence[str],
    read: Callable[[WorldState], object],
    reads: set[tuple[str, str]],
) -> dict[str, set[tuple[str, str]]]:
    """For each output of a group binding, the variables among `reads` whose perturbation
    changes it. Each variable is tried with a few values, one variable at a time; a text
    variable with every value the reader compares it against."""
    world = state.copy()
    compared = _compared(world, read, reads)
    base = list(read(world))
    moved: dict[str, set[tuple[str, str]]] = {p: set() for p in paths}
    for node, key in sorted(reads):
        if not node:
            continue  # sim time: every group reads it only as a timestamp, if at all
        variables = world.assets[node]
        original = variables[key]
        seen = compared.get((node, key), set())
        tried = {original}
        todo = _perturbations(original, seen)
        while todo:
            value = todo.pop()
            tried.add(value)
            if isinstance(value, str):  # learn what the reader compares it with next
                value = _Text(value, seen)
            variables[key] = value
            try:
                values = list(read(world))
            except Exception:  # an impossible combination of variables: no evidence
                values = base
            finally:
                variables[key] = original
            for path, before, after in zip(paths, base, values, strict=True):
                if not _same(before, after):
                    moved[path].add((node, key))
            if isinstance(original, str):
                todo = sorted(seen - tried)
    return moved


def _compared(
    world: WorldState, read: Callable[[WorldState], object], reads: set[tuple[str, str]]
) -> dict[tuple[str, str], set[str]]:
    """Every value the reader compares each text variable against, from one read with each
    text variable swapped for a recording copy."""
    compared: dict[tuple[str, str], set[str]] = {}
    originals = {}
    for node, key in reads:
        value = world.assets[node].get(key) if node else None
        if isinstance(value, str):
            originals[node, key] = value
            world.assets[node][key] = _Text(value, compared.setdefault((node, key), set()))
    try:
        read(world)
    finally:
        for (node, key), value in originals.items():
            world.assets[node][key] = value
    return compared


class _Text(str):
    """A text value that records every string it is compared with."""

    __slots__ = ("_seen",)

    def __new__(cls, value: str, seen: set[str]) -> "_Text":
        text = super().__new__(cls, value)
        text._seen = seen
        return text

    def __eq__(self, other: object) -> bool:
        if isinstance(other, str):
            self._seen.add(str(other))
        return str.__eq__(self, other)

    def __ne__(self, other: object) -> bool:
        return not self == other

    __hash__ = str.__hash__


def _perturbations(value: object, compared: set[str]) -> list[object]:
    """Values to try in place of `value`: enough to cross any threshold a reader applies."""
    if isinstance(value, bool):
        return [not value]
    if isinstance(value, int):
        return [value + 1, value * 2 + 10, value - 50, 0 if value else 1]
    if isinstance(value, float):
        return [value + 1.0, value * 2.0 + 10.0, value - 50.0, 0.0 if value else 1.0]
    if isinstance(value, str):
        return [*sorted(compared - {value}), UNMATCHED]  # UNMATCHED goes first
    return []


def _same(a: object, b: object) -> bool:
    return a == b or (a != a and b != b)  # NaN equals NaN here


class _Variables(dict):
    """One node's AssetState that records every variable read from it."""

    __slots__ = ("_node", "_seen")

    def __init__(self, node: str, variables: Mapping[str, Any], seen: set) -> None:
        super().__init__(variables)
        self._node, self._seen = node, seen

    def __getitem__(self, key: str) -> Any:
        self._seen.add((self._node, key))
        return super().__getitem__(key)

    def get(self, key: str, default: Any = None) -> Any:
        self._seen.add((self._node, key))
        return super().get(key, default)

    def __contains__(self, key: object) -> bool:
        self._seen.add((self._node, str(key)))
        return super().__contains__(key)


class _Probe:
    """A world state that records what is read from it."""

    __slots__ = ("_seen", "_time", "assets", "faults")

    def __init__(self, state: WorldState, seen: set) -> None:
        self._seen, self._time = seen, state.time
        self.assets = {n: _Variables(n, v, seen) for n, v in state.assets.items()}
        self.faults = state.faults

    @property
    def time(self) -> int:
        self._seen.add(("", "time"))
        return self._time
