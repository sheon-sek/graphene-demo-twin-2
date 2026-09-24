"""P2 cooling faults (#23): every cooling fault's Fault Preview and Clear, and the two
acceptance scenarios, all chillers stopped (v1 #5) and a tower fan failure's evidence chain."""

import re
from collections.abc import Callable
from dataclasses import dataclass

import pytest

from graphene_demo_twin.faults import STANDARD_CATALOG, FaultParams, preview_fault
from graphene_demo_twin.projection import Projector
from graphene_demo_twin.sim import Event, Scalar, Simulation
from graphene_demo_twin.sim.plant import PLANT, plant_layout
from graphene_demo_twin.world import default_domains, default_projector

START = 1_790_000_000  # Monday 2026-09-21, 22:13 local
CSC = "Chiller System Control"
CS = "Chiller_System"
CRAC3 = "CRAC/L1_CRAC3"  # serves DH03
CT1_CELL = "Cooling Towers Plant/R_P1_CT1"  # a cell of the lead's Tower Group CT-001


def _sim(plant_design) -> Simulation:
    return Simulation(plant_design, default_domains(), 7, START)


def _inject(at: int, target: str, fault: str, **params) -> Event:
    return Event(at, "fault.inject", target, {"fault": fault, **params})


def _clear(at: int, target: str, fault: str) -> Event:
    return Event(at, "fault.clear", target, {"fault": fault})


@pytest.fixture(scope="module")
def projector(asset_model, plant_design) -> Projector:
    return default_projector(asset_model, plant_design)


def _up(margin: float) -> Callable[[Scalar, Scalar], bool]:
    return lambda base, hit: hit > base + margin


def _becomes(value: Scalar) -> Callable[[Scalar, Scalar], bool]:
    return lambda base, hit: base != value and hit == value


@dataclass(frozen=True)
class Scenario:
    target: str
    point: str
    """The point that shows the fault."""
    shows: Callable[[Scalar, Scalar], bool]
    """(value without the fault, value with it) → whether the point shows it."""
    alarm: str | None
    """An alarm bit the device logic raises, or None for a fault that raises none."""
    window_s: int = 600
    recovery_s: int = 300
    """How long after Clear the point is back where the world without the fault has it."""
    tolerance: float = 0.02


SCENARIOS = {
    "chiller.trip": Scenario(
        "Chiller/R_C1",
        f"{CSC}/Running Available",
        lambda base, hit: hit == "2/3" and base == "2/4",
        "Chiller/R_C1/System Failure_Trip",
        recovery_s=960,  # the trip latches for 15 minutes after its cause has gone
    ),
    "chiller.compressor_degradation": Scenario(
        "Chiller/R_C1", "Chiller/R_C1/Input Power", _up(20.0), None, tolerance=2.0
    ),
    "chiller.condenser_fouling": Scenario(
        "Chiller/R_C1", "Chiller/R_C1/Condenser Pressure", _up(30.0), None, tolerance=2.0
    ),
    "chiller.chws_sensor_drift": Scenario(
        "Chiller/R_C1",
        f"{CSC}/Chillers/CH-001/CHWS Temp",
        _up(0.3),
        None,
        window_s=900,
        recovery_s=5,
    ),
    "chiller.hand_mode": Scenario(
        "Chiller/R_C3", "Chiller/R_C3/On_Off", _becomes(1), None, recovery_s=900
    ),
    "tower.fan_failure": Scenario(
        CT1_CELL,
        f"{CSC}/Cooling Towers/CT-001/Fan Speed",
        _up(2.0),
        f"{CT1_CELL}/System Failure_Trip",
        recovery_s=1500,
        tolerance=0.5,
    ),
    "tower.group_fan_failure": Scenario(
        CT1_CELL,
        f"{CSC}/Cooling Towers/CT-001/Alarm Status",
        _becomes("FAN TRIP"),
        "Cooling Towers Plant/R_P1_CT5/System Failure_Trip",  # another cell of the group
        recovery_s=5,
    ),
    "tower.fill_fouling": Scenario(
        CT1_CELL,
        f"{CSC}/Cooling Towers/CT-001/Fan Speed",
        _up(1.0),
        None,
        recovery_s=600,
        tolerance=0.5,
    ),
    "pump.trip": Scenario(
        "Chiller/R_CP1",
        f"{CSC}/Running Available",
        lambda base, hit: hit == "2/3" and base == "2/4",
        None,  # the sequencer takes the leg out of service before its chiller cycles
        recovery_s=5,
    ),
    "pump.bearing_wear": Scenario("Chiller/R_CP9", "Chiller/R_CP9/Power", _up(3), None),
    "pump.dp_pid_oscillation": Scenario(
        "Chiller/R_CP9",
        f"{CSC}/Controls/DP PID/Process Variable (PV)",
        lambda base, hit: abs(hit - base) > 2.0,
        None,
        window_s=300,
        tolerance=0.5,
    ),
    "valve.stuck": Scenario(
        "Chiller/R_CV3",  # CH-003's evaporator valve, standing closed
        f"{CSC}/Running Available",
        lambda base, hit: hit == "2/3" and base == "2/4",
        None,
        recovery_s=5,
    ),
    "cdu.pump_failure": Scenario(
        "TIW/CDU-01",
        f"{CSC}/Data Halls/DH8 Temperature",
        _up(0.3),
        None,
        recovery_s=1800,
        tolerance=0.1,
    ),
    "crac.compressor_failure": Scenario(
        CRAC3,
        f"{CSC}/Data Halls/DH3 Temperature",
        _up(1.0),
        f"{CRAC3}/High Pressure Alarm",
        recovery_s=1800,
        tolerance=0.1,
    ),
    "crac.filter_choke": Scenario(
        CRAC3, f"{CRAC3}/Filter Choke Alarm", _becomes(True), f"{CRAC3}/Filter Choke Alarm"
    ),
    "pahu.fan_failure": Scenario(
        "PAHU/L1_PAHU3", "PAHU/L1_PAHU3/Fan On_Off", _becomes(False), "PAHU/L1_PAHU3/HasAlarm"
    ),
    "pahu.filter_choke": Scenario(
        "PAHU/L1_PAHU3",
        "PAHU/L1_PAHU3/Filter Sensor Alarm",
        _becomes(True),
        "PAHU/L1_PAHU3/Filter Sensor Alarm",
    ),
    "fcu.fan_failure": Scenario(
        "FCU/L1_FCU3",
        "FCU/L1_FCU3/Air Differential Pressure Alarm",
        _becomes(True),
        "FCU/L1_FCU3/Air Differential Pressure Alarm",
    ),
    "fcu.filter_choke": Scenario(
        "FCU/L1_FCU3",
        "FCU/L1_FCU3/Filter Choke Alarm",
        _becomes(True),
        "FCU/L1_FCU3/Filter Choke Alarm",
    ),
    "fwu.fan_failure": Scenario(
        "FWU/R_FWU1",
        "FWU/R_FWU1/Air Differential Pressure Alarm",
        _becomes(True),
        "FWU/R_FWU1/Air Differential Pressure Alarm",
    ),
    "fwu.filter_choke": Scenario(
        "FWU/R_FWU1",
        "FWU/R_FWU1/Filter Choke Alarm",
        _becomes(True),
        "FWU/R_FWU1/Filter Choke Alarm",
    ),
    "ccu.fan_failure": Scenario(
        "~CCU-001",
        f"{CSC}/Data Halls/DH1 Temperature",
        _up(0.02),
        None,
        recovery_s=1800,
        tolerance=0.05,
    ),
    "ccu.filter_choke": Scenario(
        "~CCU-001",
        f"{CSC}/Data Halls/DH1 Temperature",
        _up(0.01),
        None,
        recovery_s=1800,
        tolerance=0.02,
    ),
}
COOLING_TYPES = {
    "Chiller",
    "Chiller Pump",
    "Chiller Valve",
    "Cooling Tower",
    "CDU",
    "PAHU",
    "FCU",
    "FWU",
    "Ceiling Cooling Units",
}


def test_every_cooling_fault_has_a_scenario():
    cooling = {s.id for s in STANDARD_CATALOG if s.asset_type in COOLING_TYPES}
    assert cooling | {"crac.compressor_failure", "crac.filter_choke"} == SCENARIOS.keys()


def _params(fault: str) -> FaultParams:
    return FaultParams(severity=STANDARD_CATALOG.get(fault).default_severity)


@pytest.mark.parametrize("fault", list(SCENARIOS))
def test_fault_preview(plant_design, asset_model, projector, fault):
    case = SCENARIOS[fault]
    sim = _sim(plant_design)
    preview = preview_fault(
        sim, projector, asset_model, case.target, fault, _params(fault), case.window_s
    )
    assert preview.affected[0].node == case.target
    diffs = {d.path: d for d in preview.diffs}
    assert case.point in diffs, f"{case.point} unchanged"
    d = diffs[case.point]
    assert case.shows(d.base, d.predicted), (d.base, d.predicted)
    alarms = {a.path for a in preview.alarms}
    if case.alarm is not None:
        assert case.alarm in alarms
    if STANDARD_CATALOG.get(fault).category == "control":  # healthy equipment raises none
        assert not {p for p in alarms if p.startswith(f"{case.target}/")}


@pytest.mark.parametrize("fault", list(SCENARIOS))
def test_clear_recovers(plant_design, projector, fault):
    case = SCENARIOS[fault]
    base, sim = _sim(plant_design), _sim(plant_design)
    severity = STANDARD_CATALOG.get(fault).default_severity
    sim.schedule(_inject(START, case.target, fault, severity=severity))
    for s in (base, sim):
        s.advance(case.window_s)
    before, hit = projector.project(base.state).values, projector.project(sim.state).values
    assert case.shows(before[case.point], hit[case.point])

    sim.schedule(_clear(sim.time, case.target, fault))
    for s in (base, sim):
        s.advance(case.recovery_s)
    assert sim.state.faults == {}
    before, after = projector.project(base.state).values, projector.project(sim.state).values
    if isinstance(before[case.point], float):
        assert after[case.point] == pytest.approx(before[case.point], abs=case.tolerance)
    else:
        assert after[case.point] == before[case.point]
    if case.alarm is not None:
        assert after[case.alarm] == before[case.alarm]


# ---- Specific mechanisms


def test_a_mistuned_dp_pid_hunts_and_settles_once_cleared(plant_design):
    sim = _sim(plant_design)
    sim.schedule(_inject(START, "Chiller/R_CP9", "pump.dp_pid_oscillation"))
    sim.advance(120)

    def swing(seconds: int) -> float:
        dps = []
        for _ in range(seconds):
            sim.step()
            dps.append(sim.state.assets[PLANT]["dp_kpa"])
        return max(dps) - min(dps)

    assert swing(120) > 10.0
    sim.schedule(_clear(sim.time, "Chiller/R_CP9", "pump.dp_pid_oscillation"))
    sim.advance(300)
    assert swing(120) < 1.0


def test_a_stuck_bypass_valve_no_longer_follows_its_command(plant_design, projector):
    sim = _sim(plant_design)
    sim.schedule(_inject(START, "Chiller/R_CV13", "valve.stuck"))
    # A lower bypass setpoint makes the bypass PID open its valves.
    sim.schedule(Event(START + 5, "command", PLANT, {"command": "bp_sp", "value": 40.0}))
    sim.advance(600)
    v = projector.project(sim.state).values
    cv = v[f"{CSC}/Controls/Bypass PID/Control Variable"]
    assert v[f"{CS}/Bypass Valves/BV-002/Position"] == pytest.approx(cv, abs=0.5)
    assert abs(v[f"{CS}/Bypass Valves/BV-001/Position"] - cv) > 2.0


def test_a_chiller_left_in_hand_runs_outside_the_sequencer(plant_design, projector):
    base, sim = _sim(plant_design), _sim(plant_design)
    sim.schedule(_inject(START, "Chiller/R_C3", "chiller.hand_mode"))
    for s in (base, sim):
        s.advance(900)
    v, b = projector.project(sim.state).values, projector.project(base.state).values
    assert v["Chiller/R_C3/Auto_Manual"] == 0 and v["Chiller/R_C3/On_Off"] == 1
    assert v[f"{CSC}/Chillers/CH-003/Mode"] == "HAND"
    # The sequencer makes room: it still runs as many chillers as the demand needs.
    assert v[f"{CSC}/Running Chillers"] == b[f"{CSC}/Running Chillers"]
    assert v["Chiller/R_C2/On_Off"] == 0
    assert not v["Chiller/R_C3/HasAlarm"]


def test_chw_sensor_drift_disagrees_with_the_water(plant_design, projector):
    sim = _sim(plant_design)
    sim.schedule(_inject(START, "Chiller/R_C1", "chiller.chws_sensor_drift", severity=1.0))
    sim.advance(1800)
    v = projector.project(sim.state).values
    c = sim.state.assets["Chiller/R_C1"]
    assert c["drift_c"] == pytest.approx(2.0, abs=0.01)
    assert v[f"{CSC}/Chillers/CH-001/CHWS Temp"] == pytest.approx(c["chws_c"] + 2.0)
    assert v[f"{CS}/Chillers/CH-001/TS-01/Temperature"] == v[f"{CSC}/Chillers/CH-001/CHWS Temp"]
    assert v[f"{CSC}/Sensors/Temperature/CHWS TS"] == pytest.approx(14.0, abs=0.1)


def test_a_crac_compressor_failure_leaves_the_lag_compressor(plant_design, projector):
    sim = _sim(plant_design)
    sim.schedule(_inject(START, CRAC3, "crac.compressor_failure"))
    sim.advance(600)
    v = projector.project(sim.state).values
    assert v[f"{CRAC3}/Compressor Capacity"] == 0.0
    assert v[f"{CRAC3}/Compressor 2 Capacity"] == pytest.approx(100.0)
    assert v[f"{CRAC3}/On_Off"] == 1 and v[f"{CRAC3}/HasAlarm"] is True


# ---- Acceptance scenarios

CHW_SUPPLY = re.compile(
    r"(Chilled Water Supply Temp(erature)?|CHWS Temp|CHWS TS|TS-01/Temperature"
    r"|BT-01/Temperature|Chilled Water Supply (Inlet|Outlet) Temp"
    r"|Buffer Tanks/BT-00\d/(Inlet|Outlet) Temp)$"
)
"""Every point that observes the chilled water the plant supplies, in the UDTs and the
Plant Views. An isolated buffer tank keeps its charge, but its outlet pipe stands and the
sensor in it warms toward the plant room."""


def test_all_chillers_stopped_warms_the_chilled_water_on_every_observer(plant_design, projector):
    """v1 #5: no point may go on reporting nominal chilled water once the plant has none."""
    base, sim = _sim(plant_design), _sim(plant_design)
    for leg in plant_layout(plant_design).legs:
        sim.schedule(_inject(START, leg.chiller, "chiller.trip"))
    for s in (base, sim):
        s.advance(900)
    assert sim.state.assets[PLANT]["running_count"] == 0
    b, v = projector.project(base.state).values, projector.project(sim.state).values
    observers = [p for p in v if CHW_SUPPLY.search(p) and not p.endswith(" SP")]
    groups = {p.split("/")[0] for p in observers} | {"/".join(p.split("/")[:2]) for p in observers}
    assert {"FCU", "FWU", "Buffer Tank", f"{CSC}/Chillers", f"{CS}/Cooling Blocks"} <= groups
    assert f"{CS}/Main Headers/TS-01/Temperature" in observers
    assert sum(p.startswith(f"{CS}/Cooling Blocks/") for p in observers) == 16
    tanks = [p for p in observers if "Buffer Tank" in p]
    assert len(tanks) == 8 * 4, tanks  # UDT inlet and outlet, Plant View inlet and outlet
    for path in observers:
        assert v[path] > 16.0, path  # nominal is 14 °C
    # The CRACs are DX: none of their points observes the chilled water.
    assert not [p for p in v if p.startswith("CRAC/") and "Water" in p]
    # The halls on chilled water warm.
    assert v[f"{CSC}/Data Halls/DH8 Temperature"] > b[f"{CSC}/Data Halls/DH8 Temperature"] + 1


def test_a_tower_fan_failure_is_read_from_the_points_as_a_chain(plant_design, projector):
    """One fault fails CT-001's fans: its condenser water warms, then CH-001's condenser
    pressure, then its power; then the sequencer brings on another chiller. Each link is read
    from points alone, in that order."""
    base, sim = _sim(plant_design), _sim(plant_design)
    cells = plant_layout(plant_design).legs[0].cells
    sim.schedule(_inject(START, CT1_CELL, "tower.group_fan_failure"))
    b = projector.project(base.state).values
    links = {  # (point, how far it must move to count as changed)
        "cw": (f"{CSC}/Cooling Towers/CT-001/CWS Temp", 0.05),
        "pressure": ("Chiller/R_C1/Condenser Pressure", 2.0),
        "power": ("Chiller/R_C1/Input Power", 2.0),
    }
    first: dict[str, int] = {}
    while "stage" not in first and sim.time < START + 3600:
        sim.advance(1 if sim.time < START + 60 else 10)  # every step while the chain starts
        v = projector.project(sim.state).values
        for name, (path, margin) in links.items():
            if name not in first and v[path] > b[path] + margin:
                first[name] = sim.time
        if v[f"{CSC}/Chillers/CH-003/Commands/Start"]:
            first["stage"] = sim.time
    assert first.keys() == {"cw", "pressure", "power", "stage"}, first
    assert first["cw"] <= first["pressure"] <= first["power"] < first["stage"], first
    # By the time the sequencer answers, each link has moved well beyond noise.
    assert v[links["cw"][0]] > b[links["cw"][0]] + 3.0
    assert v[links["pressure"][0]] > b[links["pressure"][0]] + 100.0
    assert v[f"{CSC}/Chiller Loop Status"] != "NORMAL" or v["Chiller/R_C1/HasAlarm"]
    v = projector.project(sim.state).values
    assert all(v[f"{cell}/System Failure_Trip"] for cell in cells)
    assert v[f"{CSC}/Cooling Towers/CT-001/Fan Speed"] == 0.0
    # Past the chiller's trip reset the group's failure still shows, and the sequencer does
    # not start CH-001 again against it.
    for _ in range(12):
        sim.advance(150)
        v = projector.project(sim.state).values
        assert all(v[f"{cell}/System Failure_Trip"] for cell in cells), sim.time
        assert v[f"{CSC}/Cooling Towers/CT-001/Alarm Status"] == "FAN TRIP"
        assert not v[f"{CSC}/Chillers/CH-001/Commands/Start"], sim.time


def test_one_tower_fan_failure_is_made_up_by_the_rest_of_its_group(plant_design, projector):
    base, sim = _sim(plant_design), _sim(plant_design)
    sim.schedule(_inject(START, CT1_CELL, "tower.fan_failure"))
    for s in (base, sim):
        s.advance(900)
    b, v = projector.project(base.state).values, projector.project(sim.state).values
    cells = plant_layout(plant_design).legs[0].cells
    assert v[f"{CT1_CELL}/On_Off"] == 0 and v[f"{CT1_CELL}/General Alarm"] is True
    for cell in cells[1:]:
        assert v[f"{cell}/Frequency"] > b[f"{cell}/Frequency"] + 2.0, cell
    cws = f"{CSC}/Cooling Towers/CT-001/CWS Temp"
    assert v[cws] == pytest.approx(b[cws], abs=0.3)
