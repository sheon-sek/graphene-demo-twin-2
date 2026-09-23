"""Point bindings for the airside (`sim.airside`): the CRAC, PAHU, FCU and FWU units, the CDUs,
and the Cooling Blocks and Ceiling Cooling Units `Chiller_System` observes.

Every unit reports its own state. The Cooling Blocks and CCUs are Unexported Assets, so the
instrument view's `Cooling Blocks/CB-00n` and `Ceiling Cooling Units/CCU-00n` folders are
their only points: MV-01 is the block's supply control valve and MV-02 its return
isolation valve, TS-01 and TS-02 its supply and return water, BT-01 the supply as it
reaches the hall, and FM-01 its flow.
"""

from collections.abc import Callable, Iterable

from graphene_demo_twin.asset_model import AssetModel
from graphene_demo_twin.plant_design import PlantDesign
from graphene_demo_twin.projection.projector import Binding, VariableRead
from graphene_demo_twin.sim import Scalar, WorldState
from graphene_demo_twin.sim.airside import (
    CCU_TYPE,
    CRAC_TYPE,
    FRESH_AIR_TYPE,
    LIQUID_TYPE,
    chw_units,
    cooling_blocks,
    cracs,
)

CS = "Chiller_System"
M3H = 3.6
"""m³/h per L/s."""

type Point = str | Callable[[dict[str, Scalar]], Scalar]
"""An AssetState variable of the unit, or a function of its AssetState."""

CRAC_POINTS: dict[str, Point] = {
    "Auto_Manual": lambda s: 1 if s["mode"] == "auto" else 0,
    "On_Off": "running",
    "Compressor On_Off Status": lambda s: s["compressor_pct"] > 0.0,
    "Compressor Capacity": "compressor1_pct",
    "Compressor 2 Capacity": "compressor2_pct",
    "Fan Speed": "fan_pct",
    "EC Fan Speed": "fan_pct",
    "Return Air Temperature": "return_c",
    "Return Air Temperature Setpoint": "return_sp_c",
    "Return Air Relative Humidity": "return_rh_pct",
    "Supply Air Temperature": "supply_c",
    "Supply Air Temperature Setpoint": "setpoint_c",
    "Supply Air Relative Humidity": "supply_rh_pct",
    "Filter Choke Alarm": "alarm_filter",
    "High Pressure Alarm": "alarm_high_pressure",
    "System Failure_Trip": "alarm_trip",
    "Loss of Signal Alarm": "alarm_loss_of_signal",
    "HasAlarm": "has_alarm",
}
"""CRAC member → its value from the unit's AssetState."""

_AIR: dict[str, Point] = {
    "Return Air Temperature": "return_c",
    "Return Air Relative Humidity": "return_rh_pct",
    "Supply Air Temperature": "supply_c",
    "Supply Air Relative Humidity": "supply_rh_pct",
    "On_Off": "running",
    "Auto_Manual": lambda s: 1,
    "HasAlarm": "has_alarm",
    "System Failure_Trip": "alarm_trip",
}

PAHU_POINTS: dict[str, Point] = {
    **_AIR,
    "Fan On_Off": lambda s: s["running"] and s["airflow"] > 0.0,
    "Filter Sensor Alarm": "alarm_filter",
    "Main Fire Alarm": "fire_alarm",
    "Supply Air Temperature Setpoint": "setpoint_c",
    "Return Air Temperature Setpoint": "return_sp_c",
    "Return Air Relative Humidity Setpoint": "return_rh_sp_pct",
    "Supply Air Relative Humidity Setpoint": "supply_rh_sp_pct",
}

FAN_COIL_POINTS: dict[str, Point] = {
    **_AIR,
    "EC Fan Run Status": lambda s: s["running"] and s["airflow"] > 0.0,
    "Chilled Water Supply Temperature": "chws_c",
    "Chilled Water Return Temperature": "chwr_c",
    "Flowrate": lambda s: s["flow_lps"] * 60.0,
    "Energy Monitoring System": "energy_kwh",
    "Unit Static Pressure": "static_kpa",
    "Filter Choke Alarm": "alarm_filter",
    "Air Differential Pressure Alarm": "alarm_airflow",
    "Common Alarm": "has_alarm",
    "Common Fault": "alarm_fault",
    "Fault": "alarm_trip",
    "Loss of Signal Alarm": "alarm_loss_of_signal",
    "Unit Loss Communication Alarm": "alarm_loss_of_signal",
    "Master Loss Communication Alarm": "alarm_loss_of_signal",
    "Operation": lambda s: 1 if s["running"] else 0,
}
"""FCU and FWU members; `Flowrate` in L/min."""

CDU_POINTS: dict[str, Point] = {
    "Unit Running Status": "running",
    "IT Load": "it_kwh",
    "Facility Load": "facility_kwh",
    "Load Demand": lambda s: round(s["it_kw"]),
    "PUE": "pue",
    "Pump 1": "pump1_kwh",
    "Pump 2": "pump2_kwh",
    "Pump 3": "pump3_kwh",
}

BLOCK_POINTS: dict[str, Point] = {
    "MV-01/Position": "mv1_pct",
    "MV-02/Position": "mv2_pct",
    "FM-01/Flow Rate": lambda s: s["flow_lps"] * M3H,
    "TS-01/Temperature": "chws_c",
    "TS-02/Temperature": "chwr_c",
    "BT-01/Temperature": "hall_supply_c",
}


def airside_bindings(asset_model: AssetModel, design: PlantDesign) -> list[Binding]:
    bindings = list(_bindings(asset_model, design))
    missing = [b.path for b in bindings if b.path not in asset_model.points]
    if missing:
        raise ValueError(f"airside bindings name points not in the Asset Model: {missing}")
    return sorted(bindings, key=lambda b: b.path)


def _bindings(asset_model: AssetModel, design: PlantDesign) -> Iterable[Binding]:
    for crac in cracs(design):
        yield from _members(crac, crac, CRAC_POINTS)
    points = {FRESH_AIR_TYPE: PAHU_POINTS, "FCU": FAN_COIL_POINTS, "FWU": FAN_COIL_POINTS}
    points[LIQUID_TYPE] = CDU_POINTS
    for unit, type_id in chw_units(design).items():
        if type_id == CCU_TYPE:
            tag = unit.lstrip("~")
            yield Binding(f"{CS}/Ceiling Cooling Units/{tag}/Temperature", _read(unit, "supply_c"))
            continue
        members = {p.name for p in asset_model.points_of(unit)}
        yield from _members(unit, unit, {m: f for m, f in points[type_id].items() if m in members})
    yield from _folder_alarms(asset_model, (*cracs(design), *chw_units(design)))
    for block in cooling_blocks(design):
        yield from _members(block, f"{CS}/Cooling Blocks/{block.lstrip('~')}", BLOCK_POINTS)


def _folder_alarms(asset_model: AssetModel, units: Iterable[str]) -> Iterable[Binding]:
    """`<Type>/HasAlarm_<G|L1|R>`: whether any unit of that type on that floor alarms."""
    by_folder: dict[str, list[str]] = {}
    for unit in units:
        folder, _, name = unit.partition("/")
        path = f"{folder}/HasAlarm_{name.split('_')[0]}"
        if path in asset_model.points:
            by_folder.setdefault(path, []).append(unit)
    for path, members in by_folder.items():
        yield Binding(
            path, lambda s, members=tuple(members): any(s.assets[u]["has_alarm"] for u in members)
        )


def _members(node: str, folder: str, points: dict[str, Point]) -> Iterable[Binding]:
    for member, read in points.items():
        yield Binding(f"{folder}/{member}", _read(node, read))


def _read(node: str, read: Point) -> Callable[[WorldState], Scalar]:
    if isinstance(read, str):
        return VariableRead(node, read)

    def project(state: WorldState) -> Scalar:
        return read(state.assets[node])

    return project


__all__ = ["CRAC_TYPE", "airside_bindings"]
