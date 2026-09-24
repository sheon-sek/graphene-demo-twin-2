"""P3 water: supply, transfer and roof tanks, makeup to the towers and the CHW loop, and leaks."""

import pytest

from graphene_demo_twin.asset_model import SourceClass
from graphene_demo_twin.faults import STANDARD_CATALOG
from graphene_demo_twin.plant_design import Connection, ConnectionKind, PlantDesign
from graphene_demo_twin.projection import PointSource
from graphene_demo_twin.sim import Event, Simulation
from graphene_demo_twin.sim.plant import PLANT, plant_layout
from graphene_demo_twin.sim.site import SITE
from graphene_demo_twin.sim.water import (
    DETECT_L,
    LATENT_KJ_PER_KG,
    LOW_TRIP_PCT,
    WATER,
    loss_keys,
    water_layout,
)
from graphene_demo_twin.world import default_domains, default_projector

START = 1_790_000_000
CW = "Cold Water and Sanitary System"
TP = [f"{CW}/G_TP{i}" for i in (1, 2, 3)]
ROOF = [f"{CW}/R_T{i}" for i in (1, 2)]
GROUND = [f"{CW}/G_T{i}" for i in (1, 2)]
LEAK = "Water Leak Detection System"
DH03_CABLE = f"{LEAK}/Level 1/2A"
MAKEUP = "Cooling Towers Plant/R_P1_P1"


@pytest.fixture(scope="module")
def projector(asset_model, plant_design):
    return default_projector(asset_model, plant_design)


@pytest.fixture(scope="module")
def base(plant_design):
    """The world after a minute, shared read-only: fork it."""
    sim = Simulation(plant_design, default_domains(), 7, START)
    sim.advance(60)
    return sim


def _inject(sim, target, fault, **params):
    sim.schedule(Event(sim.time, "fault.inject", target, {"fault": fault, **params}))


def _clear(sim, target, fault):
    sim.schedule(Event(sim.time, "fault.clear", target, {"fault": fault}))


def _set_roof(sim, pct):
    for t in ROOF:
        s = sim.state.assets[t]
        s["volume_l"] = s["capacity_l"] * pct / 100.0


def _rewired(design, drop=(), add=()) -> PlantDesign:
    """`design` with the water connections `drop` removed and `add` added, as (source, target)."""
    water = ConnectionKind.WATER
    kept = [c for c in design.connections if not (c.kind is water and (c.source, c.target) in drop)]
    assert len(kept) == len(design.connections) - len(drop)
    return PlantDesign(
        design.version,
        design.floors,
        design.rooms.values(),
        design.assets.values(),
        design.unexported.values(),
        [*kept, *(Connection(water, s, t) for s, t in add)],
        design.it_basis.values(),
        design.shafts.values(),
    )


def _running_cells(sim, design):
    a = sim.state.assets
    return [
        cell
        for leg in plant_layout(design).legs
        if a[leg.cw_pump]["flow_lps"] > 0.0
        for cell in leg.cells
    ]


# ---- Leak detection


def test_healthy_leak_cables_report_no_leak(base, projector, plant_design):
    p = projector.project(base.state)
    cables = water_layout(plant_design).cables
    assert len(cables) == 22
    for cable in cables:
        assert p.values[f"{cable}/Status"] == 0, cable
        assert p.values[f"{cable}/Leak Position"] == 0.0, cable
        assert base.state.assets[cable]["water_l"] == 0.0


def test_a_leak_in_dh03_trips_its_level_1_cable_once_water_reaches_it(base, projector):
    sim = base.fork()
    _inject(sim, DH03_CABLE, "leak.pipe_leak", severity=0.5)
    sim.step()
    # Water has only just started to escape: the cable senses nothing yet.
    assert 0.0 < sim.state.assets[DH03_CABLE]["water_l"] < DETECT_L
    assert projector.project(sim.state).values[f"{DH03_CABLE}/Status"] == 0
    sim.advance(30)
    p = projector.project(sim.state)
    assert p.values[f"{DH03_CABLE}/Status"] == 1
    length = p.values[f"{DH03_CABLE}/Cable Length"]
    assert 0.5 < p.values[f"{DH03_CABLE}/Leak Position"] < length - 0.5
    others = [path for path in p.values if path.startswith(LEAK) and path.endswith("/Status")]
    assert [x for x in others if p.values[x]] == [f"{DH03_CABLE}/Status"]

    # The water escapes from the closed CHW loop: its pressure falls and the AC makeup
    # pumps start to hold it.
    before = base.state.assets[WATER]["loop_kpa"]
    sim.advance(600)
    assert sim.state.assets[WATER]["loop_kpa"] < before
    assert sim.state.assets["AC Makeup Tank/G_P1"]["running"]
    assert projector.project(sim.state).values["AC Makeup Tank/G_P1/On_Off"] == 1

    # Cleared, the water drains away and the cable clears once it has gone.
    _clear(sim, DH03_CABLE, "leak.pipe_leak")
    sim.advance(60)
    assert projector.project(sim.state).values[f"{DH03_CABLE}/Status"] == 1
    sim.advance(3 * 3600)
    assert sim.state.assets[DH03_CABLE]["water_l"] < DETECT_L
    assert projector.project(sim.state).values[f"{DH03_CABLE}/Status"] == 0


def test_a_leak_from_an_isolated_loop_stops_once_the_loop_is_empty(base, projector):
    """A pipe gives only the water its source holds: with the AC makeup branch shut, the loop's
    last litres reach the floor, then the pool drains and the cable clears with the fault on."""
    sim = base.fork()
    a = sim.state.assets
    branch = water_layout(sim.design).ac_branch
    a[branch]["cmd_open"] = False
    a[WATER]["loop_kpa"] = 5.0  # 50 L above empty
    _inject(sim, DH03_CABLE, "leak.pipe_leak", severity=0.5)
    sim.advance(120)
    assert not a[branch]["open"] and a[WATER]["loop_kpa"] == 0.0
    peak = a[DH03_CABLE]["water_l"]
    assert DETECT_L < peak <= 50.0
    sim.advance(60)
    assert a[DH03_CABLE]["water_l"] < peak
    sim.advance(2400)
    assert "leak.pipe_leak@" + DH03_CABLE in sim.state.faults
    assert a[DH03_CABLE]["water_l"] < DETECT_L
    assert projector.project(sim.state).values[f"{DH03_CABLE}/Status"] == 0


def test_a_leak_cable_in_a_room_with_no_pipework_is_no_leak_target(plant_design):
    targets = set(STANDARD_CATALOG.get("leak.pipe_leak").targets(plant_design))
    assert DH03_CABLE in targets and f"{LEAK}/Ground/1A" in targets
    assert f"{LEAK}/Ground/2A" not in targets  # UPS room A: no water or CHW pipe in it


# ---- Layout


def test_the_layout_follows_the_authored_water_connections(plant_design):
    layout = water_layout(plant_design)
    assert layout.roof_inlets == (f"{CW}/R_V1", f"{CW}/R_V2")
    # Swap which inlet fills which roof tank: the layout follows the pipes.
    swapped = _rewired(
        plant_design,
        drop=[(f"{CW}/R_V1", ROOF[0]), (f"{CW}/R_V2", ROOF[1])],
        add=[(f"{CW}/R_V1", ROOF[1]), (f"{CW}/R_V2", ROOF[0])],
    )
    assert water_layout(swapped).roof_inlets == (f"{CW}/R_V2", f"{CW}/R_V1")
    # Take the AC makeup branch off ground tank 1's outlet and onto tank 2's.
    moved = _rewired(
        plant_design,
        drop=[(f"{CW}/G_V4", f"{CW}/G_V10")],
        add=[(f"{CW}/G_V5", f"{CW}/G_V10")],
    )
    assert layout.ac_draw == (True, False)
    assert water_layout(moved).ac_draw == (False, True)


@pytest.mark.parametrize(
    "edge",
    [
        (f"{CW}/G_V9", f"{CW}/R_V2"),  # the riser to roof tank 2's inlet
        (f"{CW}/G_V1", f"{CW}/G_V3"),  # the mains to ground tank 2's inlet
        (f"{CW}/R_V5", f"{CW}/R_V8"),  # the domestic branch
        (f"{CW}/G_TP2", f"{CW}/G_V7"),  # a transfer pump's discharge
    ],
    ids=lambda e: "→".join(x.rsplit("/", 1)[1] for x in e),
)
def test_a_route_with_an_authored_connection_removed_is_rejected(plant_design, edge):
    with pytest.raises(ValueError, match="water network"):
        water_layout(_rewired(plant_design, drop=[edge]))


# ---- Tower water use and WUE


def test_evaporation_drift_and_blowdown_scale_with_heat_rejected(base, projector, plant_design):
    a, p = base.state.assets, base.state.assets[PLANT]
    layout = plant_layout(plant_design)
    total = 0.0
    for leg in layout.legs:
        rejected = p[f"{leg.tower}.rejected_kw"]
        n = len(leg.cells)
        evap, drift, blowdown = (a[WATER][k] * n for k in loss_keys(leg.tower))
        assert evap == pytest.approx(rejected / LATENT_KJ_PER_KG, rel=1e-6)
        if a[leg.cw_pump]["flow_lps"] > 0.0:
            assert rejected > 500.0
            assert blowdown == pytest.approx(evap / 3.0)
            assert 0.0 < drift < 0.01 * evap
        total += evap + drift + blowdown
    assert total > 1.0
    site = a[SITE]
    assert site["makeup_lph"] == pytest.approx(total * 3600.0, rel=1e-6)
    wue = projector.project(base.state).values["Dashboard/WUE"]
    assert wue == pytest.approx(site["makeup_lph"] / site["it_kw"], rel=1e-5)
    assert 0.3 < wue < 3.0


def test_in_steady_state_makeup_holds_every_basin_and_pressure(base, projector, plant_design):
    a = base.state.assets
    p = projector.project(base.state)
    for pump, cell in water_layout(plant_design).makeup.items():
        s = a[pump]
        assert s["running"] and not s["trip"]
        assert s["discharge_kpa"] == pytest.approx(s["sp_kpa"], abs=5.0)
        assert 60.0 < a[cell]["basin_pct"] < 80.0
        assert not a[cell]["basin_low"]
        assert p.values[f"{pump}/Actual Discharge Pressure"] == pytest.approx(s["discharge_kpa"])
        assert p.values[f"{pump}/HasAlarm"] is False
    for tank in ROOF + GROUND:
        assert 20 < p.values[f"{tank}/Water Level"] < 97
        assert p.values[f"{tank}/High_Low Water Level Alarm"] is False


# ---- Transfer pumps


def test_transfer_pumps_start_on_low_roof_level_and_the_standby_replaces_a_trip(base):
    sim = base.fork()
    _set_roof(sim, 55.0)
    sim.advance(5)
    a = sim.state.assets
    assert [a[p]["running"] for p in TP] == [True, False, False]
    _set_roof(sim, 35.0)
    sim.advance(5)
    assert [a[p]["running"] for p in TP] == [True, True, False]
    _inject(sim, TP[0], "transfer_pump.trip")
    sim.advance(5)
    assert [a[p]["running"] for p in TP] == [False, True, True]
    assert a[TP[0]]["trip"]
    # Filling: the roof tanks rise while the pumps run, and stop once full.
    v0 = sum(a[t]["volume_l"] for t in ROOF)
    sim.advance(60)
    assert sum(a[t]["volume_l"] for t in ROOF) > v0
    _set_roof(sim, 91.0)
    sim.advance(5)
    assert not any(a[p]["running"] for p in TP)


def test_transfer_pump_failure_drains_the_roof_tanks_at_the_rate_evaporation_implies(base):
    sim = base.fork()
    for pump in TP:
        _inject(sim, pump, "transfer_pump.trip")
    _set_roof(sim, 58.0)
    sim.step()
    a = sim.state.assets
    v0 = sum(a[t]["volume_l"] for t in ROOF)
    used = 0.0
    for _ in range(600):
        sim.step()
        used += a[WATER]["tower_makeup_lps"] + a[WATER]["domestic_lps"]
        assert not any(a[p]["running"] for p in TP)
    drained = v0 - sum(a[t]["volume_l"] for t in ROOF)
    assert a[WATER]["tower_loss_lps"] > 0.5
    assert drained == pytest.approx(used, rel=1e-6)
    # What the towers take up is what they lose: evaporation, drift and blowdown.
    assert a[WATER]["tower_makeup_lps"] == pytest.approx(a[WATER]["tower_loss_lps"], rel=0.05)


def test_empty_roof_tanks_collapse_makeup_pressure_then_basins_fall_then_towers_trip(
    base, plant_design
):
    """The order of the chain with its slow stages shortened: the roof tanks almost dry and the
    basins near their low-level trip. (test_the_whole_chain_... runs it from full.)"""
    sim = base.fork()
    for pump in TP:
        _inject(sim, pump, "transfer_pump.trip")
    sim.step()
    a = sim.state.assets
    cells = _running_cells(sim, plant_design)
    for cell in cells:
        a[cell]["basin_pct"] = LOW_TRIP_PCT + 2.0
    _set_roof(sim, 0.3)
    chillers = [leg.chiller for leg in plant_layout(plant_design).legs]
    running = [c for c in chillers if a[c]["running"]]
    cond0 = {c: a[c]["cond_kpa"] for c in running}
    times = _chain_times(sim, plant_design, cells, running, cond0, 900)
    assert times["pressure"] < times["basin"] < times["trip"] < times["condenser"], times
    assert times["pressure"] < 60
    assert times["condenser"] - times["trip"] < 600


@pytest.mark.slow
def test_the_whole_chain_from_a_transfer_pump_failure(base, plant_design):
    """Transfer pumps lost → roof tanks drain over hours → makeup pressure collapses → basins
    fall over tens of minutes → towers trip → condenser pressure climbs within minutes."""
    sim = base.fork()
    for pump in TP:
        _inject(sim, pump, "transfer_pump.trip")
    sim.step()
    a = sim.state.assets
    cells = _running_cells(sim, plant_design)
    chillers = [leg.chiller for leg in plant_layout(plant_design).legs]
    running = [c for c in chillers if a[c]["running"]]
    cond0 = {c: a[c]["cond_kpa"] for c in running}
    times = _chain_times(sim, plant_design, cells, running, cond0, 30 * 3600)
    assert times["pressure"] < times["basin"] < times["trip"] < times["condenser"], times
    assert 2 * 3600 < times["pressure"] < 24 * 3600
    assert 600 < times["trip"] - times["pressure"] < 6 * 3600
    assert times["condenser"] - times["trip"] < 900


def _chain_times(sim, design, cells, running, cond0, limit):
    a = sim.state.assets
    pumps = [p for p, c in water_layout(design).makeup.items() if c in cells]
    times: dict[str, int] = {}
    t0 = sim.time
    while len(times) < 4 and sim.time - t0 < limit:
        sim.step()
        t = sim.time - t0
        if "pressure" not in times and max(a[p]["discharge_kpa"] for p in pumps) < 50.0:
            times["pressure"] = t
            levels = {c: a[c]["basin_pct"] for c in cells}
        elif "pressure" in times and "basin" not in times:
            if all(a[c]["basin_pct"] < levels[c] - 0.2 for c in cells):
                times["basin"] = t
        if "trip" not in times and any(a[c]["trip"] for c in cells):
            times["trip"] = t
        if "condenser" not in times and "trip" in times:
            if any(a[c]["cond_kpa"] > cond0[c] + 100.0 for c in running):
                times["condenser"] = t
    assert len(times) == 4, times
    return times


# ---- Faults


def test_makeup_pump_failure_drains_only_its_cell(base, projector, plant_design):
    sim = base.fork()
    cell = water_layout(plant_design).makeup[MAKEUP]
    level0 = sim.state.assets[cell]["basin_pct"]
    _inject(sim, MAKEUP, "makeup_pump.failure")
    sim.advance(600)
    a = sim.state.assets
    assert not a[MAKEUP]["running"] and a[MAKEUP]["trip"]
    assert a[MAKEUP]["flow_lps"] == 0.0
    assert a[cell]["basin_pct"] < level0 - 0.1
    p = projector.project(sim.state)
    assert p.values[f"{MAKEUP}/System Failure_Trip"] is True
    assert p.values[f"{MAKEUP}/HasAlarm"] is True
    assert p.values["Cooling Towers Plant/HasAlarm_R_P1"] is True
    other = "Cooling Towers Plant/R_P1_P2"
    assert a[other]["running"] and not a[other]["trip"]


def test_municipal_supply_loss_stops_the_ground_tanks_refilling(base):
    sim = base.fork()
    a = sim.state.assets
    for t in GROUND:
        a[t]["volume_l"] = a[t]["capacity_l"] * 0.8
    sim.advance(5)
    assert a[f"{CW}/G_V2"]["open"] and a[WATER]["municipal_lps"] > 0.0
    _inject(sim, f"{CW}/G_V1", "water.municipal_loss")
    _set_roof(sim, 50.0)
    sim.advance(5)
    v0 = sum(a[t]["volume_l"] for t in GROUND)
    sim.advance(120)
    assert a[WATER]["municipal_lps"] == 0.0
    assert sum(a[t]["volume_l"] for t in GROUND) < v0


def test_a_roof_tank_level_sensor_fault_misleads_the_transfer_control(base, projector):
    sim = base.fork()
    _inject(sim, ROOF[0], "roof_tank.level_sensor")
    _inject(sim, ROOF[1], "roof_tank.level_sensor")
    _set_roof(sim, 55.0)
    sim.advance(5)
    a = sim.state.assets
    # The physical level asks for a transfer pump; the readings, high, do not.
    assert not any(a[p]["running"] for p in TP)
    p = projector.project(sim.state)
    assert p.values[f"{ROOF[0]}/Water Level"] > 90
    assert a[ROOF[0]]["level_pct"] == pytest.approx(55.0, abs=0.1)


@pytest.mark.parametrize("fault", ["ground_valve.stuck", "roof_valve.stuck"])
def test_every_stuck_valve_target_holds_against_a_command_and_changes_its_tank(base, fault):
    """Each target is a tank inlet whose command changes: stuck, the valve holds and its tank
    fills differently. Ground tanks at 80 % open their inlets; a roof tank at 99 % closes its
    inlet while the other, near empty, calls the lead transfer pump."""
    layout = water_layout(base.design)
    tank_of = {
        **dict(zip(layout.ground_inlets, layout.ground_tanks, strict=True)),
        **dict(zip(layout.roof_inlets, layout.roof_tanks, strict=True)),
    }
    targets = STANDARD_CATALOG.get(fault).targets(base.design)
    assert targets and set(targets) <= set(tank_of)
    for valve in targets:
        tank = tank_of[valve]
        healthy, stuck = base.fork(), base.fork()
        _inject(stuck, valve, fault)
        for sim in (healthy, stuck):
            a = sim.state.assets
            for t in layout.ground_tanks:
                a[t]["volume_l"] = a[t]["capacity_l"] * 0.8
            for t in layout.roof_tanks:
                a[t]["volume_l"] = a[t]["capacity_l"] * (0.99 if t == tank else 0.1)
            sim.advance(60)
        h, s = healthy.state.assets, stuck.state.assets
        assert s[valve]["open"] != s[valve]["cmd_open"], valve
        assert s[valve]["open"] != h[valve]["open"], valve
        assert s[tank]["volume_l"] != pytest.approx(h[tank]["volume_l"], abs=1.0), valve


# ---- Projection


def test_every_water_point_has_a_causal_source(projector, asset_model, plant_design):
    layout = water_layout(plant_design)
    nodes = [*layout.nodes, "Cold Water and Sanitary System", "Cooling Towers Plant"]
    for path, point in asset_model.points.items():
        if not path.startswith(tuple(f"{n}/" for n in nodes)):
            continue
        if point.source_class is SourceClass.STATIC_METADATA:
            continue
        assert projector.coverage.entries[path].source is not PointSource.FALLBACK, path
