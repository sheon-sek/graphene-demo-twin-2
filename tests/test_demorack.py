"""P4 Demo Rack (#27): a standalone electrical system with its own meters and breakers."""

import pytest

from graphene_demo_twin.projection import PointSource
from graphene_demo_twin.sim import Event, Simulation
from graphene_demo_twin.sim.demorack import BREAKERS, CIRCUITS, METERS, PER_BREAKER
from graphene_demo_twin.world import default_domains, default_projector

START = 1_790_000_000
DR = "DemoRack"


def _sim(design) -> Simulation:
    return Simulation(design, default_domains(), 7, START)


def _inject(at, target, fault) -> Event:
    return Event(at, "fault.inject", target, {"fault": fault})


@pytest.fixture(scope="module")
def projector(asset_model, plant_design):
    return default_projector(asset_model, plant_design)


def _values(projector, sim):
    return dict(projector.project(sim.state).values)


def test_every_demo_rack_point_is_physics(projector):
    cov = projector.coverage.entries
    stuck = [
        p
        for p, e in cov.items()
        if p.startswith(DR + "/") and e.source is PointSource.FALLBACK and e.debt
    ]
    assert stuck == []


def test_readings_are_consistent(plant_design, projector):
    sim = _sim(plant_design)
    sim.advance(30)
    v = _values(projector, sim)
    e = f"{DR}/E820/"
    circuits = sum(
        v[f"{e}{b}{n}_I{s:02d}_P"] for b in ("Ba", "Bb") for n in (1, 2, 3) for s in range(1, 22)
    )
    assert circuits > 10
    inc_p = sum(v[f"{e}Ba{n}_I01_P"] for n in (1, 2, 3))
    assert inc_p > 0
    # every breaker's meter-side power adds up to the circuits below it
    assert v[f"{DR}/GEM630/Ptot"] == pytest.approx(
        sum(sim.state.assets[BREAKERS[i]]["p_kw"] for i in METERS[f"{DR}/GEM630"].breakers),
        rel=1e-4,
    )
    assert 380 < v[f"{e}Vsys"] < 420 and 49.8 < v[f"{e}Hz"] < 50.2
    assert v[f"{DR}/GEM230/I1"] == pytest.approx(
        v[f"{DR}/GEM230/S1"] * 1000 / v[f"{DR}/GEM230/V1"], rel=1e-4
    )
    assert v[f"{DR}/GDC230/V1"] == 48.0


def test_breaker_trip_affects_only_demo_rack_points(plant_design, projector):
    base, faulted = _sim(plant_design), _sim(plant_design)
    faulted.schedule(_inject(START + 5, f"{DR}/Breaker3", "breaker.trip"))
    base.advance(20)
    faulted.advance(20)
    a, b = _values(projector, base), _values(projector, faulted)
    changed = {p for p in a if a[p] != b[p]}
    assert changed
    assert all(p.startswith(DR + "/") for p in changed), sorted(changed)[:10]
    assert b[f"{DR}/Breaker3/Trip"] == 1 and b[f"{DR}/Breaker3/OnOff"] == 0
    assert b[f"{DR}/Breaker4/Trip"] == 0
    assert b[f"{DR}/GEM630/HasAlarm"] is True or b[f"{DR}/GEM630/HasAlarm"] == 1
    assert b[f"{DR}/GPM96/HasAlarm"] in (False, 0)
    assert sim_state_kw(faulted, 2) == 0.0 and PER_BREAKER == 9 and CIRCUITS == 126


def sim_state_kw(sim, breaker_index) -> float:
    return sim.state.assets[BREAKERS[breaker_index]]["p_kw"]


def test_earth_fault_sets_ef_and_recovers_after_clear(plant_design, projector):
    sim = _sim(plant_design)
    sim.schedule(_inject(START + 2, f"{DR}/Breaker7", "breaker.earth_fault"))
    sim.schedule(
        Event(START + 10, "fault.clear", f"{DR}/Breaker7", {"fault": "breaker.earth_fault"})
    )
    sim.advance(8)
    v = _values(projector, sim)
    assert v[f"{DR}/Breaker7/EF"] == 1 and v[f"{DR}/Breaker7/Trip"] == 1
    sim.advance(45)
    v = _values(projector, sim)
    assert v[f"{DR}/Breaker7/EF"] == 0 and v[f"{DR}/Breaker7/OnOff"] == 1


def test_incomer_trip_drops_the_rack_only(plant_design, projector):
    sim = _sim(plant_design)
    sim.schedule(_inject(START + 2, f"{DR}/E820", "e820.incomer_trip"))
    sim.advance(10)
    v = _values(projector, sim)
    assert v[f"{DR}/E820/Vsys"] == 0 and v[f"{DR}/GEM630/Ptot"] == 0
    assert v[f"{DR}/E820/HasAlarm"] in (True, 1)
    assert v["Dashboard/PUE"] > 0
