"""P5 zero-fallback audit (#28): no production point is a Causal Dead End, and every point
that used to be one now follows the world."""

import pytest

from graphene_demo_twin.audit import SPARE_PORT, DeadEnd, audit
from graphene_demo_twin.projection import Binding, Projector, VariableRead
from graphene_demo_twin.sim import Event, Simulation
from graphene_demo_twin.sim.plant import plant_layout
from graphene_demo_twin.world import default_domains, default_projector

START = 1_790_000_000
GEN = "Genset/Genset 1"
CIRCUIT = "IPS/Circuit 3"


@pytest.fixture(scope="module")
def projector(asset_model, plant_design) -> Projector:
    return default_projector(asset_model, plant_design)


def _sim(plant_design) -> Simulation:
    return Simulation(plant_design, default_domains(), 7, START)


def _command(sim: Simulation, target: str, command: str, value) -> None:
    sim.schedule(Event(sim.time, "command", target, {"command": command, "value": value}))


def _inject(sim: Simulation, target: str, fault: str) -> None:
    sim.schedule(Event(sim.time, "fault.inject", target, {"fault": fault}))


def test_the_audit_is_green(asset_model, plant_design, projector):
    """The CI gate: every production point has a causal source. Only spare switch ports,
    which nothing in the Plant Design connects, are exempt."""
    report = audit(asset_model, plant_design, projector, _sim(plant_design).state)
    assert report.ok, report.summary()
    assert report.production > 7000
    assert set(report.exempt.values()) == {SPARE_PORT}
    assert all("/Ports/Port " in p for p in report.exempt)


def test_the_audit_finds_each_kind_of_dead_end(asset_model, plant_design):
    crac = "CRAC/L1_CRAC1"
    bindings = [
        Binding(f"{crac}/Return Air Temperature", VariableRead(crac, "return_c")),
        Binding(f"{crac}/Supply Air Temperature", lambda s: 18.0),
        Binding(f"{crac}/Return Air Relative Humidity", VariableRead(crac, "setpoint_c")),
    ]
    report = audit(
        asset_model, plant_design, Projector(asset_model, bindings), _sim(plant_design).state
    )
    assert f"{crac}/Return Air Temperature" not in report.dead_ends
    assert report.dead_ends[f"{crac}/Supply Air Temperature"] is DeadEnd.CONSTANT
    assert report.dead_ends[f"{crac}/Return Air Relative Humidity"] is DeadEnd.SETPOINT_MIRROR
    assert report.dead_ends[f"{crac}/On_Off"] is DeadEnd.FALLBACK
    assert not report.ok


def test_a_genset_in_manual_ignores_the_ats_and_its_emergency_stop_drops_it(
    plant_design, projector
):
    sim = _sim(plant_design)
    _command(sim, GEN, "mode", "manual")
    _command(sim, GEN, "run", True)
    sim.advance(60)
    p = projector.project(sim.state)
    assert p.values[f"{GEN}/Auto_Manual"] == 0
    assert p.values[f"{GEN}/Run Command Active"] == 1
    assert sim.state.assets[GEN]["stage"] == "running"
    _command(sim, GEN, "estop", True)
    sim.step()
    p = projector.project(sim.state)
    assert p.values[f"{GEN}/Emergency Stop"] == 1
    assert p.values[f"{GEN}/General Genset Alarm"]
    assert p.values[f"{GEN}/Engine Speed"] == 0.0


def test_an_insulation_fault_is_measured_and_located(plant_design, projector):
    sim = _sim(plant_design)
    before = projector.project(sim.state)
    assert before.values["IPS/Device Status"] and before.values["IPS/Insulation Value"] > 1000
    _inject(sim, CIRCUIT, "ips.insulation_fault")
    sim.step()
    p = projector.project(sim.state)
    assert p.values["IPS/Insulation Value"] < 50
    assert p.values[f"{CIRCUIT}/Insulation Fault"]
    assert not p.values["IPS/Circuit 1/Insulation Fault"]
    _inject(sim, CIRCUIT, "ips.ct_open")
    sim.step()
    p = projector.project(sim.state)
    assert p.values["IPS/No CT"] and not p.values[f"{CIRCUIT}/Insulation Fault"]


def test_a_seized_buffer_tank_inlet_is_bypassed_and_the_tank_leaves_service(
    plant_design, projector
):
    sim = _sim(plant_design)
    sim.advance(600)  # a leg running with its tanks in line
    leg = next(
        leg for leg in plant_layout(plant_design).legs if sim.state.assets[leg.chiller]["running"]
    )
    tank = leg.tanks[0]
    assert sim.state.assets[tank]["flow_lps"] > 0.0
    _inject(sim, tank, "buffer_tank.inlet_valve_stuck")
    sim.advance(3)
    p = projector.project(sim.state)
    assert p.values[f"{tank}/Normally Opened Valve Fail To Open"]
    assert p.values[f"{tank}/Normally Closed Valve Open Command"]
    assert p.values[f"{tank}/Normally Closed Valve Open Status"] == 1
    assert sim.state.assets[tank]["flow_lps"] == 0.0
    view = f"Chiller System Control/Buffer Tanks/BT-00{tank.rsplit('BT', 1)[1]}"
    assert p.values[f"{view}/Enabled"] is False


def test_a_tower_cell_and_a_makeup_pump_in_hand_follow_the_operator(plant_design, projector):
    sim = _sim(plant_design)
    sim.advance(600)
    leg = next(
        leg for leg in plant_layout(plant_design).legs if sim.state.assets[leg.chiller]["running"]
    )
    cell = leg.cells[0]
    assert sim.state.assets[cell]["running"]
    _command(sim, cell, "mode", "hand")
    _command(sim, cell, "run", False)
    pump = "Cooling Towers Plant/R_P1_P1"
    _command(sim, pump, "mode", "hand")
    _command(sim, pump, "run", False)
    sim.advance(5)
    p = projector.project(sim.state)
    assert p.values[f"{cell}/Auto_Manual"] == 0 and p.values[f"{cell}/On_Off"] == 0
    tower = f"Chiller System Control/Cooling Towers/{leg.tower}/Mode"
    assert p.values[tower] == "HAND"
    assert p.values[f"{pump}/Auto_Manual"] == 0 and p.values[f"{pump}/On_Off"] == 0


def test_a_fresh_air_handler_in_hand_stops_when_switched_off(plant_design, projector):
    sim = _sim(plant_design)
    pahu = next(a.path for a in plant_design.assets.values() if a.type_id == "PAHU" and a.room)
    _command(sim, pahu, "mode", "hand")
    _command(sim, pahu, "run", False)
    sim.step()
    p = projector.project(sim.state)
    assert p.values[f"{pahu}/Auto_Manual"] == 0 and not p.values[f"{pahu}/On_Off"]
