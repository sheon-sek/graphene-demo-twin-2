"""P2 chiller plant hydraulics and its Controllers (#21)."""

import math

import pytest

from graphene_demo_twin.projection import PointSource, Projector
from graphene_demo_twin.sim import Event, Simulation
from graphene_demo_twin.sim.plant import (
    CHILLER_KWR,
    PLANT,
    plant_layout,
)
from graphene_demo_twin.world import default_domains, default_projector

START = 1_790_000_000  # Monday 2026-09-21, 22:13 local
ROTATION = 1_790_231_220  # Thursday 2026-09-24, 14:27:00 local: a scheduled rotation
CSC = "Chiller System Control"
CS = "Chiller_System"
HALLS = [f"~IT-DH0{i}" for i in range(1, 9)]
CH = {
    f"CH-00{i}": c
    for i, c in enumerate(("Chiller/R_C1", "Chiller/R_C2", "Chiller/R_C3", "~CH-004"), 1)
}
M3H = 3.6
"""m³/h per L/s."""


def _sim(plant_design, start: int = START) -> Simulation:
    return Simulation(plant_design, default_domains(), 7, start)


def _surge(at: int, severity: float = 0.3) -> list[Event]:
    """Every hall's IT Load surges towards its design load."""
    return [
        Event(at, "fault.inject", it, {"fault": "it.load_surge", "severity": severity})
        for it in HALLS
    ]


def _command(at: int, target: str, command: str, value) -> Event:
    return Event(at, "command", target, {"command": command, "value": value})


@pytest.fixture(scope="module")
def projector(asset_model, plant_design) -> Projector:
    return default_projector(asset_model, plant_design)


def _running(sim: Simulation) -> list[str]:
    return [
        leg.name
        for leg in plant_layout(sim.design).legs
        if sim.state.assets[leg.chiller]["running"]
    ]


# ---- Layout


def test_the_plant_is_four_legs_each_with_its_own_pumps_valves_tanks_and_tower_group(
    plant_design,
):
    layout = plant_layout(plant_design)
    assert [leg.name for leg in layout.legs] == list(CH)
    assert [leg.chiller for leg in layout.legs] == list(CH.values())
    assert [leg.tower for leg in layout.legs] == ["CT-001", "CT-002", "CT-003", "CT-004"]
    first = layout.legs[0]
    assert (first.chw_pump, first.cw_pump) == ("Chiller/R_CP1", "Chiller/R_CP5")
    assert (first.evap_valve, first.cond_valve, first.header_valve) == (
        "Chiller/R_CV1",
        "Chiller/R_CV5",
        "Chiller/R_CV9",
    )
    assert first.tanks == ("Buffer Tank/R_BT1", "Buffer Tank/R_BT2")
    assert len(first.cells) == 5 and all(len(leg.cells) == 5 for leg in layout.legs)
    assert layout.secondary == "Chiller/R_CP9"
    assert layout.bypass == tuple(f"Chiller/R_CV{i}" for i in range(13, 17))


# ---- Steady state


def test_the_plant_starts_steady_with_enough_chillers_for_the_demand(plant_design):
    sim = _sim(plant_design)
    plant = sim.state.assets[PLANT]
    running = _running(sim)
    assert running == ["CH-001", "CH-002"]  # the lead first, then in rotation order
    assert (
        len(running)
        == plant["required"]
        == math.ceil(plant["demand_kw"] / (CHILLER_KWR * plant["load_limit_pct"] / 100.0))
    )
    assert plant["chws_c"] == pytest.approx(plant["chws_sp_c"], abs=0.05)
    before = dict(plant)
    sim.advance(300)
    for name in ("chws_c", "chwr_c", "dp_kpa", "flow_lps", "supply_kw"):
        assert sim.state.assets[PLANT][name] == pytest.approx(before[name], rel=0.02), name
    assert _running(sim) == running


def test_chiller_physics_is_load_dependent(plant_design):
    sim = _sim(plant_design)
    c = dict(sim.state.assets["Chiller/R_C1"])
    assert 3.0 < c["cop"] < 9.0
    assert c["power_kw"] == pytest.approx(c["cooling_kw"] / c["cop"])
    assert c["cond_kpa"] > c["evap_kpa"] > 300.0
    assert c["disch_hi_c"] > c["cw_out_c"] > c["cw_in_c"]
    assert 0.0 < c["current_pct"] < 100.0
    # More load on the same plant: more power, higher lift, more current.
    for e in _surge(sim.time):
        sim.schedule(e)
    sim.advance(170)  # before any stage up
    hot = sim.state.assets["Chiller/R_C1"]
    assert hot["cooling_kw"] > c["cooling_kw"]
    assert hot["power_kw"] > c["power_kw"]
    assert hot["current_pct"] > c["current_pct"]
    assert hot["cond_kpa"] > c["cond_kpa"]


# ---- Staging


def test_staging_follows_demand_with_realistic_delays(plant_design, projector):
    sim = _sim(plant_design)
    plant = sim.state.assets[PLANT]
    plant["load_limit_pct"] = 55.0  # two chillers carry 3.85 MW: the surge needs a third
    for e in _surge(sim.time):
        sim.schedule(e)
    wait = int(plant["stage_up_wait_s"])
    for _ in range(900):
        sim.step()
        if plant["pending"]:
            break
    assert plant["pending"] and plant["required"] == 2
    sim.advance(wait - 5)
    assert plant["required"] == 2, "the demand must persist for the stage-up wait"
    assert not sim.state.assets["Chiller/R_C3"]["run_cmd"]
    sim.advance(10)
    assert plant["required"] == 3
    ch3 = sim.state.assets["Chiller/R_C3"]
    assert ch3["run_cmd"] and not ch3["running"]  # starting: valves, pumps, then compressor
    p = projector.project(sim.state).values
    assert p[f"{CSC}/Chillers/CH-003/Commands/Start"] is True
    assert p["Chiller/R_C3/On_Off"] == 0
    assert p[f"{CSC}/Chillers/CH-003/Sequence State"] != "RUNNING"
    started = None
    for t in range(600):
        sim.step()
        if sim.state.assets["Chiller/R_C3"]["running"]:
            started = t
            break
    assert started is not None and started > 30, "the start sequence takes time"
    sim.advance(400)
    assert _running(sim) == ["CH-001", "CH-002", "CH-003"]
    assert_commands_agree_with_feedback(projector.project(sim.state).values)


@pytest.mark.slow
def test_staging_down_waits_for_the_demand_to_stay_low(plant_design):
    sim = _sim(plant_design)
    plant = sim.state.assets[PLANT]
    plant["load_limit_pct"] = 50.0  # two chillers no longer carry the demand
    sim.advance(900)
    assert _running(sim) == ["CH-001", "CH-002", "CH-003"]
    plant["load_limit_pct"] = 85.0  # now they do again
    sim.advance(int(plant["stage_down_wait_s"]) - 10)
    assert len(_running(sim)) == 3
    sim.advance(400)
    assert plant["required"] == 2
    assert _running(sim) == ["CH-001", "CH-002"]


def test_min_and_max_chillers_bound_staging(plant_design):
    sim = _sim(plant_design)
    sim.state.assets[PLANT]["min_chillers"] = 3
    sim.advance(int(sim.state.assets[PLANT]["stage_up_wait_s"]) + 400)
    assert len(_running(sim)) == 3
    sim = _sim(plant_design)
    sim.state.assets[PLANT]["max_chillers"] = 2
    for e in _surge(sim.time):
        sim.schedule(e)
    sim.advance(600)
    assert len(_running(sim)) == 2
    assert sim.state.assets[PLANT]["required"] == 2


def test_the_load_limit_caps_each_chiller_and_stages_more(plant_design):
    sim = _sim(plant_design)
    sim.state.assets[PLANT]["load_limit_pct"] = 50.0
    sim.advance(60)
    for leg in plant_layout(plant_design).legs[:2]:
        assert sim.state.assets[leg.chiller]["cooling_kw"] <= 0.5 * CHILLER_KWR + 1.0
    sim.advance(int(sim.state.assets[PLANT]["stage_up_wait_s"]) + 400)
    assert len(_running(sim)) == 3


def test_a_chiller_that_loses_power_is_replaced_by_the_next_to_start(plant_design, projector):
    sim = _sim(plant_design)
    feeder = "Meter/Level 2_MSB A_4"  # CH-001's feeder
    assert projector.project(sim.state).values[f"{CSC}/Next To Start"] == "CH-003"
    sim.schedule(Event(sim.time, "fault.inject", feeder, {"fault": "gpm96.breaker_trip"}))
    sim.advance(5)
    assert not sim.state.assets["Chiller/R_C1"]["running"]
    assert sim.state.assets["Chiller/R_C3"]["run_cmd"]
    sim.advance(400)
    assert _running(sim) == ["CH-002", "CH-003"]
    assert_commands_agree_with_feedback(projector.project(sim.state).values)


def test_hand_mode_follows_the_operator_not_the_sequencer(plant_design, projector):
    sim = _sim(plant_design)
    sim.schedule(_command(sim.time, "~CH-004", "mode", "hand"))
    sim.schedule(_command(sim.time, "~CH-004", "run", True))
    sim.advance(400)
    assert "CH-004" in _running(sim)
    p = projector.project(sim.state)
    assert p.values[f"{CSC}/Chillers/CH-004/Mode"] == "HAND"
    assert p.values[f"{CSC}/Chillers/CH-004/Status"] == "RUNNING"
    sim.schedule(_command(sim.time, "Chiller/R_C2", "enable", False))
    sim.advance(400)
    assert "CH-002" not in _running(sim)
    assert projector.project(sim.state).values[f"{CSC}/Chillers/CH-002/Enabled"] is False


def test_the_rotation_schedule_hands_the_lead_on_make_before_break(plant_design, projector):
    sim = _sim(plant_design, ROTATION - 60)
    before = projector.project(sim.state).values
    assert before[f"{CSC}/Rotation Schedule/Current Lead"] == "CH-001"
    sim.advance(61)
    p = projector.project(sim.state).values
    lead = p[f"{CSC}/Rotation Schedule/Current Lead"]
    assert lead == "CH-004"  # the fewest run hours
    assert (
        p[f"{CSC}/Rotation Schedule/Rotation Count"]
        == before[f"{CSC}/Rotation Schedule/Rotation Count"] + 1
    )
    assert p[f"{CSC}/Rotation Schedule/Last Rotation"] == "2026-09-24 14:27:00"
    assert p[f"{CSC}/Rotation Schedule/Last Scheduled Key"] == "2026-09-24|Thursday|14:27"
    assert len(_running(sim)) == 2  # the outgoing chiller runs until the lead is up
    sim.advance(600)
    assert _running(sim) == ["CH-001", "CH-004"]


# ---- Safety trips


def test_losing_the_towers_trips_the_chiller_on_high_condenser_pressure(plant_design, projector):
    sim = _sim(plant_design)
    for cell in plant_layout(plant_design).legs[0].cells:
        sim.state.assets[cell]["constraint.fan_loss"] = 1.0
    cond = sim.state.assets["Chiller/R_C1"]["cond_kpa"]
    sim.advance(120)
    assert sim.state.assets["Chiller/R_C1"]["cond_kpa"] > cond
    for _ in range(3600):
        sim.step()
        if sim.state.assets["Chiller/R_C1"]["trip"]:
            break
    c = dict(sim.state.assets["Chiller/R_C1"])
    assert c["trip"] == "HIGH CONDENSER PRESSURE"
    assert not c["running"]
    p = projector.project(sim.state).values
    assert p["Chiller/R_C1/System Failure_Trip"] is True
    assert p["Chiller/R_C1/Unit Safety Fault Code"] is True
    assert p["Chiller/R_C1/General Alarm"] is True
    assert p[f"{CSC}/Chillers/CH-001/Status"] == "TRIPPED"
    assert p[f"{CSC}/Alarms/Critical Count"] >= 1
    sim.advance(5)
    assert sim.state.assets["Chiller/R_C3"]["run_cmd"]


# ---- Chilled water reaches the air units


def test_losing_every_chiller_warms_the_chilled_water_and_the_halls(plant_design):
    base, sim = _sim(plant_design), _sim(plant_design)
    for chiller in CH.values():
        sim.schedule(_command(sim.time, chiller, "enable", False))
    for s in (base, sim):
        s.advance(900)
    plant = sim.state.assets[PLANT]
    assert plant["chws_c"] > base.state.assets[PLANT]["chws_c"] + 2.0
    assert sim.state.assets["DH01"]["temp_c"] > base.state.assets["DH01"]["temp_c"] + 0.5


# ---- Command and feedback, and the Plant Views


def assert_commands_agree_with_feedback(values) -> None:
    for name, chiller in CH.items():
        ch = f"{CSC}/Chillers/{name}"
        on = values[f"{ch}/Chiller On_Off Status"] == "ON"
        assert values[f"{ch}/Commands/Start"] is on, name
        assert values[f"{ch}/Commands/Stop"] is not on, name
        if not chiller.startswith("~"):
            assert values[f"{chiller}/On_Off"] == int(on), name
    for i in range(1, 5):
        bv = values[f"{CS}/Bypass Valves/BV-00{i}/Position"]
        assert bv == pytest.approx(values[f"{CSC}/Controls/Bypass PID/Control Variable"], abs=0.5)
    speed = values[f"{CSC}/Pumps/P-CHWR-01/Speed"]
    assert speed == pytest.approx(values[f"{CSC}/Controls/DP PID/Control Variable"], abs=0.5)
    pv = values[f"{CSC}/Controls/DP PID/Process Variable (PV)"]
    assert pv == pytest.approx(values[f"{CSC}/Controls/DP PID/Setpoint (SP)"], rel=0.02)
    for i in range(1, 5):
        tower = f"{CSC}/Cooling Towers/CT-00{i}"
        assert values[f"{tower}/CWS Temp"] <= values[f"{tower}/CWR Temp"] + 1e-6


def test_command_and_feedback_agree_in_steady_state(plant_design, projector):
    sim = _sim(plant_design)
    assert_commands_agree_with_feedback(projector.project(sim.state).values)
    sim.advance(600)
    assert_commands_agree_with_feedback(projector.project(sim.state).values)


def _counterparts(design) -> dict[str, tuple[str, float]]:
    """Chiller_System point → (the UDT or supervisory point for the same quantity, the
    factor from the counterpart's unit to this one's)."""
    pairs: dict[str, tuple[str, float]] = {}
    for i, leg in enumerate(plant_layout(design).legs, 1):
        cs, csc = f"{CS}/Chillers/{leg.name}", f"{CSC}/Chillers/{leg.name}"
        ct = f"{CSC}/Cooling Towers/{leg.tower}"
        pairs |= {
            f"{cs}/FM-01/Flow Rate": (f"{csc}/Chilled Water Flow Rate", M3H),
            f"{cs}/TS-01/Temperature": (f"{csc}/Chilled Water Supply Temp", 1.0),
            f"{cs}/TS-02/Temperature": (f"{csc}/Chilled Water Return Temp", 1.0),
            f"{cs}/TS-03/Temperature": (f"{ct}/CWS Temp", 1.0),
            f"{cs}/TS-04/Temperature": (f"{ct}/CWR Temp", 1.0),
            f"{cs}/WCC-00{i}/Cooling Load": (f"{csc}/Cooling Load", 1.0),
            f"{cs}/WCC-00{i}/Electrical Power": (f"{csc}/Electrical Power", 1.0),
            f"{cs}/WCC-00{i}/Load": (f"{csc}/Load", 1.0),
        }
        if not leg.chiller.startswith("~"):
            pairs[f"{cs}/WCC-00{i}/Electrical Power"] = (f"{leg.chiller}/Input Power", 1.0)
    pairs |= {
        f"{CS}/Main Headers/TS-01/Temperature": (f"{CSC}/Sensors/Temperature/CHWS TS", 1.0),
        f"{CS}/Main Headers/TS-02/Temperature": (f"{CSC}/Sensors/Temperature/CHWR TS", 1.0),
        f"{CS}/Main Headers/TS-03/Temperature": (f"{CSC}/Sensors/Temperature/CWS TS", 1.0),
        f"{CS}/Main Headers/TS-04/Temperature": (f"{CSC}/Sensors/Temperature/CWR TS", 1.0),
        f"{CS}/Main Headers/FM-01/Flow Rate": (f"{CSC}/Sensors/Flow/CHWS FM", 1.0),
        f"{CS}/Main Headers/DPS-01/Differential Pressure": (
            f"{CSC}/Controls/DP PID/Process Variable (PV)",
            1.0,
        ),
        f"{CS}/Main Headers/DPS-02/Differential Pressure": (
            f"{CSC}/Controls/Bypass PID/Process Variable (PV)",
            1.0,
        ),
    }
    return pairs


@pytest.mark.parametrize("when", ["steady", "staging up"])
def test_chiller_system_equals_the_udt_and_supervisory_values(plant_design, projector, when):
    sim = _sim(plant_design)
    if when == "staging up":
        for e in _surge(sim.time):
            sim.schedule(e)
        sim.advance(int(sim.state.assets[PLANT]["stage_up_wait_s"]) + 100)
    v = projector.project(sim.state).values
    for path, (other, factor) in _counterparts(plant_design).items():
        assert v[path] == pytest.approx(v[other] * factor, rel=1e-6, abs=1e-6), path
    # Supervisory duplicates of one quantity agree too.
    for name in CH:
        csc = f"{CSC}/Chillers/{name}"
        assert v[f"{csc}/CHWS Temp"] == v[f"{csc}/Chilled Water Supply Temp"]
        assert v[f"{csc}/CHWR Temp"] == v[f"{csc}/Chilled Water Return Temp"]
        assert v[f"{csc}/Flow Rate"] == v[f"{csc}/Chilled Water Flow Rate"]
        assert v[f"{csc}/Power"] == v[f"{csc}/Electrical Power"]
    assert v[f"{CSC}/CHWS Temp"] == v[f"{CSC}/Sensors/Temperature/CHWS TS"]
    assert v[f"{CSC}/Flow Rate"] * M3H == pytest.approx(v[f"{CSC}/Sensors/Flow/CHWS FM"])
    assert v[f"{CSC}/Pumps/P-CHWR-01/Flow"] == v[f"{CSC}/Sensors/Flow/CHWS FM"]
    assert v[f"{CSC}/Differential Pressure"] == v[f"{CSC}/Controls/DP PID/Process Variable (PV)"]
    for i, leg in enumerate(plant_layout(plant_design).legs, 1):
        for valve, view in (
            (leg.evap_valve, f"{CS}/Chillers/{leg.name}/MV-01/Position"),
            (leg.cond_valve, f"{CS}/Chillers/{leg.name}/MV-02/Position"),
            (leg.header_valve, f"{CS}/Header Motorized Valves/MV-00{i}/Position"),
            (plant_layout(plant_design).bypass[i - 1], f"{CS}/Bypass Valves/BV-00{i}/Position"),
        ):
            assert v[f"{valve}/On_Off"] == int(v[view] > 1.0), view


def test_every_chiller_system_point_outside_the_cooling_blocks_is_driven(projector):
    fallback = {
        p
        for p in projector.coverage.paths(PointSource.FALLBACK)
        if p.startswith(f"{CS}/") and "/Cooling Blocks/" not in p and "/Ceiling Cooling" not in p
    }
    assert fallback == set()


def test_the_plant_equipment_has_a_causal_source(projector, asset_model):
    prefixes = ("Chiller/", "Buffer Tank/", "Cooling Towers Plant/R_P1_CT", f"{CSC}/Chillers/")
    prefixes += ("Cooling Towers Plant/R_P2_CT", "Cooling Towers Plant/HasAlarm")
    prefixes += (f"{CSC}/Cooling Towers/", f"{CSC}/Buffer Tanks/", f"{CSC}/Controls/")
    prefixes += (f"{CSC}/Pumps/", f"{CSC}/Rotation Schedule/", f"{CSC}/Sensors/")
    fallback = [
        p
        for p in projector.coverage.paths(PointSource.FALLBACK)
        if p.startswith(prefixes)
        and not projector.coverage.entries[p].source_class.name == "STATIC_METADATA"
    ]
    assert fallback == []
