"""P4 fire protection and lifts (#26)."""

import pytest

from graphene_demo_twin.faults.preview import _hops
from graphene_demo_twin.projection import PointSource, Projector
from graphene_demo_twin.sim import Event, Simulation
from graphene_demo_twin.sim.life_safety import (
    DOOR_CLOSED,
    DOOR_OPEN,
    FIRE_PUMP_KW,
    LIFT_IDLE_KW,
    fire_zones,
)
from graphene_demo_twin.world import default_domains, default_projector

START = 1_790_000_000  # Monday 2026-09-21, 22:13 local
FPS = "Fire Protection System"
L1Z2 = f"{FPS}/Level 1/Zone 2"
FZ_DH02 = "~FZ-L1-Z2"
PAHU2 = "PAHU/L1_PAHU2"
LIFT1 = "Lift Monitoring System/Lift 1"


def _sim(plant_design) -> Simulation:
    return Simulation(plant_design, default_domains(), 7, START)


def _inject(at: int, target: str, fault: str, **params) -> Event:
    return Event(at, "fault.inject", target, {"fault": fault, **params})


def _clear(at: int, target: str, fault: str) -> Event:
    return Event(at, "fault.clear", target, {"fault": fault})


@pytest.fixture(scope="module")
def projector(asset_model, plant_design) -> Projector:
    return default_projector(asset_model, plant_design)


def _fire_points(asset_model) -> list[str]:
    return [p for p in asset_model.points if p.startswith(f"{FPS}/")]


def test_every_fire_and_lift_point_is_driven_by_the_world(asset_model, projector):
    paths = [p for p in asset_model.points if p.startswith((f"{FPS}/", "Lift Monitoring System/"))]
    assert len(paths) == 68
    assert {projector.coverage.entries[p].source for p in paths} == {PointSource.PHYSICS}


def test_each_zone_stops_the_fresh_air_handlers_of_its_own_rooms(plant_design):
    by_zone = {z.id: z.fresh_air for z in fire_zones(plant_design)}
    assert by_zone[FZ_DH02] == (PAHU2,)
    assert by_zone["~FZ-L2-Z1"] == ("PAHU/R_PAHU1",)  # roof-mounted, serves DH05
    assert by_zone["~FZ-G-Z3"] == ("PAHU/G_PAHU2", "PAHU/G_PAHU3")  # UPS B & battery rooms
    assert by_zone["~FZ-R-Z1"] == ()


def test_the_base_world_has_no_fire_alarm(plant_design, projector, asset_model):
    sim = _sim(plant_design)
    sim.advance(60)
    p = projector.project(sim.state)
    assert not any(p.values[f] for f in _fire_points(asset_model))
    assert not any(p.values[f"PAHU/{u}/Main Fire Alarm"] for u in ("L1_PAHU2", "R_PAHU1"))


def test_a_fire_in_dh02_alarms_level_1_zone_2_only_and_stops_its_pahu(
    plant_design, projector, asset_model
):
    """The #26 acceptance scenario."""
    base, sim = _sim(plant_design), _sim(plant_design)
    sim.schedule(_inject(START, FZ_DH02, "fire.room_fire"))
    base.advance(600)
    sim.advance(600)
    b, p = projector.project(base.state), projector.project(sim.state)

    alarmed = {f for f in _fire_points(asset_model) if p.values[f]}
    zone = {f for f in alarmed if f.startswith(f"{L1Z2}/")}
    pumps = {f for f in alarmed if f.rsplit("/", 1)[1].startswith("FP")}
    assert zone == {f"{L1Z2}/SD1", f"{L1Z2}/SD2", f"{L1Z2}/AV1"}  # sprinklers flowing
    assert pumps and alarmed == zone | pumps  # no other zone alarms; the fire pumps run
    assert sim.state.assets["~G-Z3-FP1"]["power_kw"] == FIRE_PUMP_KW

    assert p.values[f"{PAHU2}/Main Fire Alarm"] is True
    assert p.values[f"{PAHU2}/On_Off"] == 0
    others = [a for a in plant_design.assets if a.startswith("PAHU/") and a != PAHU2]
    assert not any(p.values[f"{u}/Main Fire Alarm"] for u in others)
    assert all(p.values[f"{u}/On_Off"] == b.values[f"{u}/On_Off"] for u in others)

    # The hall warms (the fire, and the fresh air it no longer gets) and grows more humid.
    hall, clean = sim.state.assets["DH02"], base.state.assets["DH02"]
    assert hall["temp_c"] > clean["temp_c"] + 0.5
    assert hall["dew_point_c"] > clean["dew_point_c"] + 1.0
    assert hall["rh_pct"] > clean["rh_pct"] + 1.0
    dh = "Chiller System Control/Data Halls/DH2 Temperature"
    assert p.values[dh] > b.values[dh] + 0.5
    em = "Environment Monitoring/Level 1/DH02/Avg Cold Aisle Humidity"
    assert p.values[em] > b.values[em]
    # Other halls do not see it.
    assert abs(sim.state.assets["DH03"]["temp_c"] - base.state.assets["DH03"]["temp_c"]) < 0.05


def test_losing_fresh_air_alone_warms_and_humidifies_the_hall(plant_design):
    """A false alarm stops L1_PAHU2 without any fire: the hall still responds."""
    base, sim = _sim(plant_design), _sim(plant_design)
    sim.schedule(_inject(START, "~L1-Z2-SD1", "smoke_detector.false_alarm"))
    base.advance(600)
    sim.advance(600)
    assert sim.state.assets[FZ_DH02]["smoke_pct_m"] == 0.0
    assert sim.state.assets[PAHU2]["running"] is False
    hall, clean = sim.state.assets["DH02"], base.state.assets["DH02"]
    assert hall["temp_c"] > clean["temp_c"] + 0.02
    assert hall["dew_point_c"] > clean["dew_point_c"] + 1.0
    assert hall["rh_pct"] > clean["rh_pct"] + 1.0


def test_an_unsprinklered_fire_trips_the_heat_detector(plant_design, projector):
    sim = _sim(plant_design)
    sim.schedule(_inject(START, "~FZ-L1-Z1", "fire.room_fire"))  # DH01: SD1, HD1, CP1
    sim.advance(300)
    p = projector.project(sim.state)
    z1 = f"{FPS}/Level 1/Zone 1"
    assert p.values[f"{z1}/SD1"] and p.values[f"{z1}/HD1"]
    assert not p.values[f"{z1}/CP1"]  # nobody pressed it
    assert not any(p.values[f] for f in p.values if f.startswith(FPS) and "/FP" in f)
    assert p.values["PAHU/L1_PAHU1/Main Fire Alarm"] is True


def test_a_faulted_detector_shows_fault_but_misses_the_fire(plant_design, projector):
    sim = _sim(plant_design)
    sim.schedule(_inject(START, "~L1-Z1-HD1", "heat_detector.fault"))
    sim.advance(5)
    p = projector.project(sim.state)
    assert p.values[f"{FPS}/Level 1/Zone 1/HD1"] is True  # in fault on the panel
    assert p.values["PAHU/L1_PAHU1/Main Fire Alarm"] is False  # a fault is not an alarm
    assert sim.state.assets["~L1-Z1-HD1"]["fault"] is True


def test_clearing_a_fire_lets_the_zone_reset_and_the_pahu_restart(plant_design, projector):
    sim = _sim(plant_design)
    sim.schedule(_inject(START, FZ_DH02, "fire.room_fire"))
    sim.schedule(_clear(START + 180, FZ_DH02, "fire.room_fire"))
    sim.advance(180)
    assert sim.state.assets[FZ_DH02]["alarm"]
    sim.advance(1200)
    p = projector.project(sim.state)
    assert not sim.state.assets[FZ_DH02]["alarm"]
    assert p.values[f"{PAHU2}/Main Fire Alarm"] is False
    assert sim.state.assets[PAHU2]["running"] is True
    assert "constraint.fire_alarm" not in sim.state.assets[PAHU2]


def test_the_lifts_run_a_traffic_model(plant_design, projector):
    sim = _sim(plant_design)
    seen = {"level": set(), "direction": set(), "door": set()}
    for _ in range(120):
        sim.advance(30)
        p = projector.project(sim.state)
        for lift in (1, 2, 3):
            f = f"Lift Monitoring System/Lift {lift}"
            seen["level"].add(p.values[f"{f}/Lift Level"])
            seen["direction"].add(p.values[f"{f}/Direction"])
            seen["door"].add(p.values[f"{f}/Door Status"])
            assert 1 <= p.values[f"{f}/Lift Level"] <= 4
    assert seen["level"] >= {1, 2, 3, 4}
    assert {"Up", "Down"} <= seen["direction"]
    assert seen["door"] == {DOOR_OPEN, DOOR_CLOSED}
    assert projector.project(sim.state).values[f"{LIFT1}/Moving Until"] > START * 1000


def test_a_fire_alarm_recalls_the_lifts_to_the_ground_floor(plant_design, projector):
    sim = _sim(plant_design)
    sim.schedule(_inject(START, "~L1-Z4-CP1", "call_point.false_alarm"))
    sim.advance(60)
    p = projector.project(sim.state)
    for lift in (1, 2, 3):
        f = f"Lift Monitoring System/Lift {lift}"
        assert p.values[f"{f}/Lift Level"] == 1
        assert p.values[f"{f}/Door Status"] == DOOR_OPEN


def test_a_stuck_lift_stops_with_its_doors_shut_and_resumes_on_clear(plant_design):
    sim = _sim(plant_design)
    for _ in range(3600):  # until Lift 1 is travelling
        sim.step()
        if sim.state.assets["~LIFT-1"]["phase"] == "moving":
            break
    lift = sim.state.assets["~LIFT-1"]
    target = lift["target"]
    sim.schedule(_inject(sim.time, "~LIFT-1", "lift.stuck"))
    sim.advance(120)
    held = dict(lift)
    sim.advance(60)
    assert lift["level"] == held["level"] and lift["door"] == DOOR_CLOSED
    assert lift["phase"] == "moving" and lift["target"] == target
    sim.schedule(_clear(sim.time, "~LIFT-1", "lift.stuck"))
    sim.advance(30)
    assert lift["level"] == target


def _until_lift_1_travels_up_past_ground(sim: Simulation) -> dict:
    lift = sim.state.assets["~LIFT-1"]
    for _ in range(3600):
        sim.step()
        if lift["phase"] == "moving" and lift["target"] > lift["level"] >= 2:
            return lift
    raise AssertionError("Lift 1 never travelled up")


def test_a_recall_turns_a_travelling_lift_back_without_opening_on_the_way(plant_design):
    sim = _sim(plant_design)
    lift = _until_lift_1_travels_up_past_ground(sim)
    sim.schedule(_inject(sim.time, "~L1-Z4-CP1", "call_point.false_alarm"))
    for _ in range(60):
        sim.step()
        if lift["door"] == DOOR_OPEN:
            break
        assert lift["phase"] == "moving"
    assert lift["level"] == 1 and lift["door"] == DOOR_OPEN


def test_the_fire_zone_reaches_its_rooms_pahus_devices_pumps_and_lifts(plant_design):
    """ADR-0002: every consequence of a zone fire follows authored connections."""
    hops = _hops(plant_design, FZ_DH02)
    for node in ("DH02", PAHU2, "~L1-Z2-SD1", "~L1-Z2-AV1", "~G-Z3-FP1", "~LIFT-1"):
        assert hops.get(node) is not None, node


def test_lifts_stop_and_draw_nothing_without_supply(plant_design):
    sim = _sim(plant_design)
    lift = _until_lift_1_travels_up_past_ground(sim)
    sim.advance(2)
    assert sim.state.assets["~LIFT-1"]["power_kw"] > LIFT_IDLE_KW
    sim.schedule(_inject(sim.time, "Meter/Meter14", "gpm96.breaker_trip"))
    sim.advance(5)
    held = dict(lift)
    sim.advance(120)
    assert lift["level"] == held["level"] and lift["door"] == DOOR_CLOSED
    assert all(sim.state.assets[f"~LIFT-{n}"]["power_kw"] == 0.0 for n in (1, 2, 3))
    assert sim.state.assets["Meter/Meter14"]["p_kw"] == 0.0


def test_fire_pumps_do_not_run_without_supply(plant_design):
    pumps = ("~G-Z3-FP1", "~L1-SA-FP1", "~L2-SA-FP1", "~R-SA-FP1")
    sim = _sim(plant_design)
    sim.schedule(_inject(START, "Meter/Level 1_DB_24", "gpqm144.breaker_trip"))
    sim.schedule(_inject(START, FZ_DH02, "fire.room_fire"))
    sim.advance(600)
    assert sim.state.assets["~L1-Z2-AV1"]["alarm"]  # the sprinklers flow
    assert not any(sim.state.assets[p]["running"] for p in pumps)
    assert all(sim.state.assets[p]["power_kw"] == 0.0 for p in pumps)
