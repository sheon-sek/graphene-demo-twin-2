"""Stand-in P0 models that give the fault framework something causal to act on.

Control-network reachability, reduced to what a fault needs to propagate along the Plant
Design. P4 comm quality replaces it; the variable contract it reads from faults
(`quality.*`) stays. The DX CRAC units it once stood in for are modelled in `sim.airside`.
"""

import functools
from collections.abc import Iterable

from graphene_demo_twin.plant_design import ConnectionKind, PlantDesign
from graphene_demo_twin.sim.engine import StepContext
from graphene_demo_twin.sim.events import Event
from graphene_demo_twin.sim.state import AssetState, WorldState


class PlaceholderNetworkDomain:
    """Reachability over the control network: an asset whose communication is lost, and
    every node downstream of it over `net` connections, reports `comm` uncertain or bad.
    Projection turns that into point quality."""

    settling_s = 0

    def __init__(self, fault_types: Iterable[str]) -> None:
        self._types = frozenset(fault_types)
        """Asset types that can lose communication themselves."""

    def initial(self, ctx: StepContext) -> dict[str, AssetState]:
        return {node: {"comm": "good"} for node in comm_nodes(ctx.design, self._types)}

    def handles(self, event: Event, design: PlantDesign) -> bool:
        return False

    def apply(self, event: Event, state: WorldState) -> None:
        raise AssertionError("no events")

    def step(self, state: WorldState, ctx: StepContext) -> None:
        nodes = comm_nodes(ctx.design, self._types)
        degraded: dict[str, str] = {}
        for node in nodes:
            loss = state.assets[node].get("quality.comm_loss", 0.0)
            if loss <= 0.0:
                continue
            quality = "bad" if loss >= 0.5 else "uncertain"
            reached = (node, *ctx.design.downstream(node, ConnectionKind.NET, transitive=True))
            for n in reached:
                if degraded.get(n) != "bad":
                    degraded[n] = quality
        for node in nodes:
            state.assets[node]["comm"] = degraded.get(node, "good")


# ---- Plant Design lookups (derived once per design; the design is immutable)


@functools.cache
def assets_of(design: PlantDesign, type_id: str) -> tuple[str, ...]:
    return tuple(a.path for a in design.assets.values() if a.type_id == type_id and a.room)


@functools.cache
def comm_nodes(design: PlantDesign, fault_types: frozenset[str]) -> tuple[str, ...]:
    """Every node whose communication can be lost: assets of `fault_types`, and every node
    on the control network."""
    nodes = [a.path for a in design.assets.values() if a.type_id in fault_types and a.room]
    for c in design.connections:
        if c.kind is ConnectionKind.NET:
            nodes += (c.source, c.target)
    return tuple(dict.fromkeys(nodes))
