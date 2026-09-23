"""Point bindings for the stand-in P0 models in `sim.placeholder`; P1 and P2 physics replace
them."""

from collections.abc import Callable, Iterable

from graphene_demo_twin.asset_model import AssetModel
from graphene_demo_twin.plant_design import PlantDesign
from graphene_demo_twin.projection.projector import Binding, QualityBinding
from graphene_demo_twin.projection.values import Quality
from graphene_demo_twin.sim import Scalar, WorldState
from graphene_demo_twin.sim.placeholder import CRAC_TYPE, assets_of, comm_nodes

CRAC_POINTS: dict[str, Callable[[dict[str, Scalar]], Scalar]] = {
    "Auto_Manual": lambda s: 1 if s["mode"] == "auto" else 0,
    "On_Off": lambda s: s["running"],
    "Compressor On_Off Status": lambda s: s["compressor_pct"] > 0.0,
    "Compressor Capacity": lambda s: s["compressor_pct"],
    "Compressor 2 Capacity": lambda s: s["compressor_pct"],
    "Fan Speed": lambda s: s["fan_pct"],
    "EC Fan Speed": lambda s: s["fan_pct"],
    "Return Air Temperature": lambda s: s["return_c"],
    "Supply Air Temperature": lambda s: s["supply_c"],
    "Supply Air Temperature Setpoint": lambda s: s["setpoint_c"],
    "Filter Choke Alarm": lambda s: s["alarm_filter"],
    "High Pressure Alarm": lambda s: s["alarm_high_pressure"],
    "System Failure_Trip": lambda s: s["alarm_trip"],
    "Loss of Signal Alarm": lambda s: s["alarm_loss_of_signal"],
    "HasAlarm": lambda s: s["has_alarm"],
}
"""CRAC member → its value from the unit's AssetState."""

SUPERVISOR_POINTS = frozenset({"Loss of Signal Alarm"})
"""Members the supervisor computes itself, so they keep good quality when the asset's
communication is lost."""


def placeholder_bindings(asset_model: AssetModel, design: PlantDesign) -> list[Binding]:
    """Every CRAC reports its unit state."""
    bindings: list[Binding] = []
    for crac in assets_of(design, CRAC_TYPE):
        for member, read in CRAC_POINTS.items():
            bindings.append(Binding(f"{crac}/{member}", _read(crac, read)))
    missing = [b.path for b in bindings if b.path not in asset_model.points]
    if missing:
        raise ValueError(f"placeholder bindings name points not in the Asset Model: {missing}")
    return sorted(bindings, key=lambda b: b.path)


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


def _read(node: str, read: Callable[[dict[str, Scalar]], Scalar]) -> Callable[[WorldState], Scalar]:
    def project(state: WorldState) -> Scalar:
        return read(state.assets[node])

    return project
