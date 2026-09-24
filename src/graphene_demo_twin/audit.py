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

Exactly one rule exempts production points, and the report counts every point it exempts.
A switch port with no peer in the Plant Design (a spare) reports link down, no traffic and
no errors. It keeps doing so whatever happens, because nothing is plugged into it. When the
switch fails, those points still respond through their quality.
"""

import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from graphene_demo_twin.asset_model import AssetModel, SourceClass
from graphene_demo_twin.plant_design import PlantDesign
from graphene_demo_twin.projection import PointSource, Projector
from graphene_demo_twin.sim import WorldState
from graphene_demo_twin.sim.network import network


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
SPARE_PORT = "spare switch port: nothing is connected in the Plant Design"


@dataclass(frozen=True, slots=True)
class AuditReport:
    production: int
    """Production points checked."""
    dead_ends: Mapping[str, DeadEnd]
    """Every production point that is a Causal Dead End, and why."""
    exempt: Mapping[str, str]
    """Production points the one exemption rule excuses, with the reason."""

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
            f"exempt ({SPARE_PORT}): {len(self.exempt)}",
        ]
        lines += [f"  {d.value}: {p}" for p, d in sorted(self.dead_ends.items())]
        return "\n".join(lines)


def audit(
    asset_model: AssetModel, design: PlantDesign, projector: Projector, state: WorldState
) -> AuditReport:
    """Audit every production point of `projector` against `state`, typically the steady
    initial state of the world."""
    coverage = projector.coverage
    exempt = {p: SPARE_PORT for p in spare_port_paths(asset_model, design)}
    production = {
        path
        for path, e in coverage.entries.items()
        if not e.support and e.source_class is not SourceClass.STATIC_METADATA
    }
    dead: dict[str, DeadEnd] = {}
    seen: set[tuple[str, str]] = set()
    probe = _Probe(state, seen)
    for path in production - exempt.keys():
        if coverage.entries[path].source is PointSource.FALLBACK:
            dead[path] = DeadEnd.FALLBACK
    for paths, read in projector.readers():
        mine = [p for p in paths if p in production and p not in exempt]
        if not mine:
            continue
        seen.clear()
        read(probe)
        reads = set(seen)
        for path in mine:
            if not reads:
                dead[path] = DeadEnd.CONSTANT
            elif asset_model.point(path).source_class in MEASURED and all(
                SETPOINT_VARIABLE.search(key) for _, key in reads
            ):
                dead[path] = DeadEnd.SETPOINT_MIRROR
    return AuditReport(
        len(production),
        MappingProxyType(dict(sorted(dead.items()))),
        MappingProxyType(exempt),
    )


def spare_port_paths(asset_model: AssetModel, design: PlantDesign) -> Iterator[str]:
    """Every point of a switch port that the Plant Design leaves unconnected."""
    net = network(design)
    for view, switch in net.switches.items():
        for port in net.ports[switch]:
            if port.peer is None:
                prefix = f"{view}/Ports/Port {port.number:02d}/"
                for p in asset_model.points_of(view):
                    if p.path.startswith(prefix):
                        yield p.path


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
