"""Point quality for the stand-in P0 control-network model in `sim.placeholder`."""

from collections.abc import Callable, Iterable

from graphene_demo_twin.asset_model import AssetModel
from graphene_demo_twin.plant_design import PlantDesign
from graphene_demo_twin.projection.projector import QualityBinding
from graphene_demo_twin.projection.values import Quality
from graphene_demo_twin.sim import WorldState
from graphene_demo_twin.sim.placeholder import comm_nodes

SUPERVISOR_POINTS = frozenset({"Loss of Signal Alarm"})
"""Members the supervisor computes itself, so they keep good quality when the asset's
communication is lost."""


def placeholder_quality(
    asset_model: AssetModel, design: PlantDesign, fault_types: Iterable[str]
) -> list[QualityBinding]:
    """Every point of an asset whose `comm` is not good takes that quality, except those the
    supervisor computes itself."""
    bindings = []
    for node in comm_nodes(design, frozenset(fault_types)):
        if node not in asset_model.assets:
            continue
        paths = tuple(
            p.path for p in asset_model.points_of(node) if p.name not in SUPERVISOR_POINTS
        )
        bindings.append(QualityBinding(paths, _comm(node)))
    return bindings


def _comm(node: str) -> Callable[[WorldState], Quality]:
    def read(state: WorldState) -> Quality:
        return Quality(state.assets[node]["comm"])

    return read
