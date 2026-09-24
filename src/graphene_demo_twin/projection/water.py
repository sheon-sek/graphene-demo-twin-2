"""Point bindings for the water network and leak detection (`sim.water`).

A tank's Water Level is its level transmitter's reading, which a sensor fault can corrupt;
the tank's water itself is `level_pct`. Alarm bits are plain AssetState variables, so a Fault
Preview compares them without a call.
"""

from collections.abc import Callable, Iterable

from graphene_demo_twin.asset_model import AssetModel
from graphene_demo_twin.plant_design import PlantDesign
from graphene_demo_twin.projection.projector import Binding, VariableRead
from graphene_demo_twin.sim import Scalar, WorldState
from graphene_demo_twin.sim.water import CW, MAKEUP_PF, MAKEUP_VOLTS, water_layout

type Point = str | Callable[[dict[str, Scalar]], Scalar]


def _on(s: dict[str, Scalar]) -> int:
    return int(bool(s["running"]))


TANK_POINTS: dict[str, Point] = {
    "Water Level": lambda s: round(s["sensed_pct"]),
    "High_Low Water Level Alarm": "alarm",
    "HasAlarm": "alarm",
}
VALVE_POINTS: dict[str, Point] = {"On_Off": lambda s: int(bool(s["open"]))}
PUMP_POINTS: dict[str, Point] = {
    "Auto_Manual": lambda s: 1 if s["mode"] == "auto" else 0,
    "HasAlarm": "has_alarm",
    "Incoming Power Status": "powered",
    "On_Off": _on,
    "System Failure_Trip": "trip",
}
AC_MAKEUP_POINTS: dict[str, Point] = {"On_Off": _on, "Power": lambda s: round(s["power_kw"])}
MAKEUP_POINTS: dict[str, Point] = {
    **PUMP_POINTS,
    "Actual Discharge Pressure": "discharge_kpa",
    "Discharge Pressure Setpoint": "sp_kpa",
    "Current": lambda s: 1000.0 * s["power_kw"] / (3**0.5 * MAKEUP_VOLTS * MAKEUP_PF),
    "Output Frequency": lambda s: 0.5 * s["speed_pct"],
    "VSD Speed Control": "speed_pct",
    "Power": "power_kw",
    "Voltage": lambda s: MAKEUP_VOLTS if s["powered"] else 0.0,
    "Unit Operating Hours": lambda s: s["run_s"] / 3600.0,
}
CABLE_POINTS: dict[str, Point] = {"Status": "status", "Leak Position": "position_m"}


def water_bindings(asset_model: AssetModel, design: PlantDesign) -> list[Binding]:
    bindings = list(_bindings(asset_model, design))
    missing = [b.path for b in bindings if b.path not in asset_model.points]
    if missing:
        raise ValueError(f"water bindings name points not in the Asset Model: {missing}")
    return sorted(bindings, key=lambda b: b.path)


def _bindings(asset_model: AssetModel, design: PlantDesign) -> Iterable[Binding]:
    layout = water_layout(design)
    groups: list[tuple[Iterable[str], dict[str, Point]]] = [
        ((*layout.ground_tanks, *layout.roof_tanks), TANK_POINTS),
        (layout.valves, VALVE_POINTS),
        ((*layout.transfer, *layout.boosters), PUMP_POINTS),
        (layout.ac_makeup, AC_MAKEUP_POINTS),
        (layout.makeup, MAKEUP_POINTS),
        (layout.cables, CABLE_POINTS),
    ]
    for nodes, points in groups:
        for node in nodes:
            members = {p.name for p in asset_model.points_of(node)}
            for member, read in points.items():
                if member in members:
                    yield Binding(f"{node}/{member}", _read(node, read))
    for floor in ("G", "R"):
        alarms = [
            (n, "alarm" if n in (*layout.ground_tanks, *layout.roof_tanks) else "has_alarm")
            for n in (*layout.ground_tanks, *layout.roof_tanks, *layout.transfer, *layout.boosters)
            if n.startswith(f"{CW}/{floor}_")
        ]
        yield Binding(
            f"{CW}/HasAlarm_{floor}",
            lambda s, alarms=tuple(alarms): any(s.assets[n][k] for n, k in alarms),
        )


def _read(node: str, read: Point) -> Callable[[WorldState], Scalar]:
    if isinstance(read, str):
        return VariableRead(node, read)

    def project(state: WorldState) -> Scalar:
        return read(state.assets[node])

    return project


__all__ = ["water_bindings"]
