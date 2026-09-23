"""P2 airside units and Cooling Blocks (#22)."""

import pytest

from graphene_demo_twin.plant_design import ConnectionKind
from graphene_demo_twin.projection import PointSource, Projector
from graphene_demo_twin.sim import Simulation
from graphene_demo_twin.sim.airside import (
    CHW_UNIT_TYPES,
    COMPRESSOR1_SHARE,
    COOLING_BLOCK_TYPE,
    CRAC_TYPE,
    FRESH_AIR_TYPE,
    LIQUID_TYPE,
    NOMINAL_FAN_PCT,
    chw_units,
    cooling_blocks,
    crac_condensing_c,
)
from graphene_demo_twin.sim.placeholder import assets_of
from graphene_demo_twin.sim.thermal import PLANT, air_suppliers, served_room, zones
from graphene_demo_twin.world import default_domains, default_projector

START = 1_790_000_000
HALLS = [f"DH0{i}" for i in range(1, 9)]
SECONDARY = "Chiller/R_CP9"
CS = "Chiller_System"
M3H = 3.6
"""m³/h per L/s."""


def _sim(plant_design, seed: int = 7) -> Simulation:
    return Simulation(plant_design, default_domains(), seed, START)


def _lose_chilled_water(sim: Simulation) -> None:
    """The secondary pump trips: no chilled water reaches any air unit."""
    sim.state.assets[SECONDARY]["constraint.trip"] = 1.0


@pytest.fixture(scope="module")
def projector(asset_model, plant_design) -> Projector:
    return default_projector(asset_model, plant_design)


@pytest.fixture(scope="module")
def starved(plant_design) -> tuple[Simulation, Simulation]:
    """(the Base World, the same world 600 s after it lost its chilled water)."""
    base, sim = _sim(plant_design), _sim(plant_design)
    _lose_chilled_water(sim)
    base.advance(600)
    sim.advance(600)
    return base, sim


# ---- Acceptance: chilled-water loss


def test_chilled_water_loss_starves_the_chilled_water_units(plant_design, starved):
    base, sim = starved
    a, b = sim.state.assets, base.state.assets
    assert a[PLANT]["delivery"] == 0.0
    for unit, type_id in chw_units(plant_design).items():
        assert b[unit]["cooling_kw"] > 0.0, unit
        assert a[unit]["cooling_kw"] <= 0.02 * b[unit]["cooling_kw"], unit
        assert a[unit]["flow_lps"] == 0.0, unit
        if type_id == LIQUID_TYPE:
            assert a[unit]["removed_kw"] == pytest.approx(0.0, abs=1e-6), unit
    for block in cooling_blocks(plant_design):
        assert a[block]["flow_lps"] == 0.0, block
        assert b[block]["flow_lps"] > 0.0, block


def test_the_dx_cracs_keep_cooling_and_load_up_without_chilled_water(plant_design, starved):
    base, sim = starved
    a, b = sim.state.assets, base.state.assets
    for crac in assets_of(plant_design, CRAC_TYPE):
        assert a[crac]["running"], crac
        assert a[crac]["cooling_kw"] > b[crac]["cooling_kw"], crac
        assert a[crac]["compressor_pct"] > b[crac]["compressor_pct"], crac
    # The hall units stage their lag compressor once the lead one is full, and speed their
    # fans up once the return air passes its setpoint; DH08, which lost its liquid cooling
    # as well, gets there within ten minutes.
    staged = sped_up = 0
    for hall in HALLS:
        crac = next(n for n, _ in air_suppliers(plant_design, hall) if "CRAC" in n)
        s = a[crac]
        assert b[crac]["compressor2_pct"] == 0.0 and b[crac]["fan_pct"] == NOMINAL_FAN_PCT
        if s["compressor_pct"] > 100.0 * COMPRESSOR1_SHARE:
            assert s["compressor1_pct"] == pytest.approx(100.0) and s["compressor2_pct"] > 0.0
            staged += 1
        else:
            assert s["compressor2_pct"] == 0.0
        if s["return_c"] > s["return_sp_c"]:
            assert s["fan_pct"] > NOMINAL_FAN_PCT
            sped_up += 1
    assert a["CRAC/R_CRAC4"]["compressor2_pct"] > 0.0 and staged >= 1 and sped_up >= 1


def test_halls_warm_according_to_their_unit_mix(plant_design, starved):
    base, sim = starved
    a, b = sim.state.assets, base.state.assets
    rise = {h: (a[h]["temp_c"] - b[h]["temp_c"]) / b[h]["heat_kw"] * 1000.0 for h in HALLS}
    for hall in HALLS:
        assert a[hall]["temp_c"] > b[hall]["temp_c"] + 0.5, hall
        assert a[hall]["cooling_kw"] < a[hall]["heat_kw"], hall
    # L1 halls keep CRAC + FCU + CCU, L2 halls CRAC + the larger FWU + CCU, and DH08 also
    # loses its liquid-cooled pod: the bigger the chilled-water share, the faster it warms.
    l1 = [rise[h] for h in HALLS[:4]]
    l2 = [rise[h] for h in HALLS[4:7]]
    assert min(l2) > max(l1)
    assert rise["DH08"] > max(l2)


# ---- Acceptance: supply and return consistent with zone state and CHW


def test_unit_air_and_water_temperatures_agree_with_the_zone_and_the_plant(plant_design, projector):
    sim = _sim(plant_design)
    sim.advance(60)
    a = sim.state.assets
    p = projector.project(sim.state)
    plant = a[PLANT]
    for unit in (*assets_of(plant_design, CRAC_TYPE), *chw_units(plant_design)):
        s = a[unit]
        zone = a[served_room(plant_design, unit)]
        if "return_c" not in s:
            continue  # a CDU moves no air
        assert s["return_c"] == pytest.approx(zone["temp_c"], abs=0.05), unit
        assert s["return_rh_pct"] == pytest.approx(zone["rh_pct"], abs=0.5), unit
        assert s["supply_c"] < s["return_c"], unit
        # The supply air holds the zone's moisture, so it is more humid at its cooler
        # temperature, and never supersaturated.
        assert s["return_rh_pct"] < s["supply_rh_pct"] <= 100.0, unit
    for unit, type_id in chw_units(plant_design).items():
        s = a[unit]
        assert s["chws_c"] == pytest.approx(plant["chws_c"], abs=1e-9), unit
        assert s["chwr_c"] > s["chws_c"], unit
        if type_id != LIQUID_TYPE:
            # A coil cannot cool the air below the water that feeds it.
            assert s["supply_c"] >= s["chws_c"] - 1e-9, unit
    for unit in chw_units(plant_design):
        if unit.startswith(("FCU/", "FWU/")):
            assert p.values[f"{unit}/Chilled Water Supply Temperature"] == pytest.approx(
                a[unit]["chws_c"], abs=1e-3
            )
            assert p.values[f"{unit}/Return Air Temperature"] == pytest.approx(
                a[unit]["return_c"], abs=1e-3
            )
    for crac in assets_of(plant_design, CRAC_TYPE):
        assert p.values[f"{crac}/Return Air Relative Humidity"] == pytest.approx(
            a[crac]["return_rh_pct"], abs=1e-3
        )


def test_zone_cooling_is_what_its_units_deliver(plant_design):
    sim = _sim(plant_design)
    sim.advance(30)
    a = sim.state.assets
    for zone in zones(plant_design):
        delivered = sum(a[u]["cooling_kw"] for u in plant_design.upstream(zone, ConnectionKind.AIR))
        assert a[zone]["cooling_kw"] == pytest.approx(delivered, rel=0.02, abs=1.0), zone


def test_cooling_blocks_carry_their_units_water(plant_design, projector):
    sim = _sim(plant_design)
    sim.advance(60)
    a = sim.state.assets
    p = projector.project(sim.state)
    plant = a[PLANT]
    direct = 0.0
    for unit in chw_units(plant_design):
        if SECONDARY in plant_design.upstream(unit, ConnectionKind.CHW):
            direct += a[unit]["flow_lps"]
    blocks = cooling_blocks(plant_design)
    total = direct + sum(a[b]["flow_lps"] for b in blocks)
    assert total == pytest.approx(plant["flow_lps"], rel=1e-6)
    for block, units in blocks.items():
        b = a[block]
        flow = sum(a[u]["flow_lps"] for u in units)
        assert b["flow_lps"] == pytest.approx(flow, rel=1e-9)
        mixed = sum(a[u]["flow_lps"] * a[u]["chwr_c"] for u in units) / flow
        assert b["chwr_c"] == pytest.approx(mixed, rel=1e-9)
        assert b["chws_c"] == pytest.approx(plant["chws_c"], abs=1e-9)
        tag = block.lstrip("~")
        folder = f"{CS}/Cooling Blocks/{tag}"
        assert p.values[f"{folder}/FM-01/Flow Rate"] == pytest.approx(b["flow_lps"] * M3H, rel=1e-4)
        assert p.values[f"{folder}/TS-01/Temperature"] == pytest.approx(b["chws_c"], abs=1e-4)
        assert p.values[f"{folder}/TS-02/Temperature"] == pytest.approx(b["chwr_c"], abs=1e-4)
        assert 0.0 < p.values[f"{folder}/MV-01/Position"] <= 100.0
        assert p.values[f"{folder}/MV-02/Position"] == 100.0
    # The flow-weighted return of every unit is the plant's return.
    returns = [(a[u]["flow_lps"], a[u]["chwr_c"]) for u in chw_units(plant_design)]
    mixed = sum(f * t for f, t in returns) / sum(f for f, _ in returns)
    assert mixed == pytest.approx(plant["chwr_c"], abs=0.3)


# ---- Units


def test_crac_head_pressure_rises_with_the_outdoor_air():
    temps = [crac_condensing_c(dry, 0.5, 0.0) for dry in (10.0, 25.0, 40.0)]
    assert temps == sorted(temps) and temps[0] < temps[-1] - 20.0
    assert crac_condensing_c(30.0, 0.5, 0.5) > crac_condensing_c(30.0, 0.5, 0.0)


def test_a_fire_alarm_shuts_the_fresh_air_handler_down(plant_design, projector):
    base, sim = _sim(plant_design), _sim(plant_design)
    pahu = "PAHU/R_PAHU1"
    sim.state.assets[pahu]["constraint.fire_alarm"] = 1.0
    base.advance(600)
    sim.advance(600)
    p = projector.project(sim.state)
    assert p.values[f"{pahu}/Main Fire Alarm"] is True
    assert p.values[f"{pahu}/On_Off"] == 0 and p.values[f"{pahu}/Fan On_Off"] == 0
    assert sim.state.assets[pahu]["airflow"] == 0.0
    assert p.values["PAHU/R_PAHU2/Main Fire Alarm"] is False
    # Without its fresh-air handler, DH05's air is no longer dried.
    assert sim.state.assets["DH05"]["dew_point_c"] > base.state.assets["DH05"]["dew_point_c"] + 0.5


def test_unit_valves_throttle_on_supply_air(plant_design):
    sim = _sim(plant_design)
    sim.advance(30)
    a = sim.state.assets
    for unit, type_id in chw_units(plant_design).items():
        if type_id == LIQUID_TYPE:
            continue
        s = a[unit]
        assert 0.0 < s["valve_pct"] <= 100.0, unit
        if s["valve_pct"] < 99.0:  # throttled: the coil holds supply air at its setpoint
            assert s["supply_c"] == pytest.approx(s["setpoint_c"], abs=0.3), unit


def test_every_airside_point_has_a_causal_source(projector):
    prefixes = ("CRAC/", "PAHU/", "FCU/", "FWU/", "TIW/", f"{CS}/Cooling Blocks/")
    prefixes += (f"{CS}/Ceiling Cooling Units/",)
    fallback = [
        p
        for p in projector.coverage.paths(PointSource.FALLBACK)
        if p.startswith(prefixes)
        and projector.coverage.entries[p].source_class.name != "STATIC_METADATA"
        and not p.endswith("/New Tag")
    ]
    assert fallback == []


def test_the_unit_catalogue_matches_the_plant_design(plant_design):
    units = chw_units(plant_design)
    by_type: dict[str, int] = {}
    for type_id in units.values():
        by_type[type_id] = by_type.get(type_id, 0) + 1
    assert by_type == {
        FRESH_AIR_TYPE: 15,
        "FCU": 5,
        "FWU": 10,
        LIQUID_TYPE: 3,
        "Ceiling Cooling Units": 8,
    }
    assert set(by_type) == CHW_UNIT_TYPES
    assert len(cooling_blocks(plant_design)) == 8
    for block in cooling_blocks(plant_design):
        assert plant_design.unexported[block].type_id == COOLING_BLOCK_TYPE
