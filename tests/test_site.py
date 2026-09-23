"""P1 site physics: weather, IT Load and the site KPIs derived from world state (#18)."""

import json
import math
import statistics

import pytest

from graphene_demo_twin.asset_model import SourceClass
from graphene_demo_twin.plant_design import PLANT_DESIGN_PATH, parse_plant_design
from graphene_demo_twin.projection import PointSource, Projector
from graphene_demo_twin.sim import Event, Noise, Simulation
from graphene_demo_twin.sim.it_load import IT_TYPE, it_utilisation
from graphene_demo_twin.sim.site import SITE, LoadClass, load_class
from graphene_demo_twin.sim.thermal import COLD_AISLE_SPREAD_C, cold_aisle_sensors
from graphene_demo_twin.sim.weather import (
    LOCAL_OFFSET_S,
    WEATHER_STATION,
    outdoor_air,
    relative_humidity,
    wet_bulb,
    with_wet_bulb_rise,
)
from graphene_demo_twin.world import default_domains, default_projector

START = 1_790_000_000
"""2026-09-21 14:13 UTC."""
DAY = 86_400
HALLS = [f"DH0{i}" for i in range(1, 9)]
IT = {h: f"~IT-{h}" for h in HALLS}
FLOOR = {h: "Level 1" if h <= "DH04" else "Level 2" for h in HALLS}
WX = "Chiller System Control/Weather"
DASH = "Dashboard"


def _sim(plant_design, seed: int = 7, start: int = START) -> Simulation:
    return Simulation(plant_design, default_domains(), seed, start)


def _inject(at: int, target: str, fault: str, **params) -> Event:
    return Event(at, "fault.inject", target, {"fault": fault, **params})


def _clear(at: int, target: str, fault: str) -> Event:
    return Event(at, "fault.clear", target, {"fault": fault})


def _local_hour(t: int) -> float:
    return ((t + LOCAL_OFFSET_S) % DAY) / 3600


@pytest.fixture(scope="module")
def projector(asset_model, plant_design) -> Projector:
    return default_projector(asset_model, plant_design)


# ---- Plant Design


def test_weather_and_it_load_are_unexported_assets_observed_through_plant_views(plant_design):
    station = plant_design.unexported[WEATHER_STATION]
    assert station.type_id == "Weather Station"
    assert station.observed_by == (WX,)
    assert plant_design.room_of(WEATHER_STATION).outdoor
    for hall in HALLS:
        node = plant_design.asset(IT[hall])
        assert node.type_id == IT_TYPE and node.unexported and node.room == hall
        assert plant_design.unexported[IT[hall]].observed_by == (
            f"Dashboard/Energy/Floors/{FLOOR[hall]}/Data Halls/{hall}",
            f"Environment Monitoring/{FLOOR[hall]}/{hall}",
        )
        # The hall's three branch circuits feed its IT equipment.
        n = hall[-1]
        assert plant_design.upstream(IT[hall], "power") == tuple(f"BCPM/{n}L{k}" for k in (1, 2, 3))


def test_the_it_basis_comes_from_the_plant_design(plant_design):
    basis = plant_design.it_basis
    assert set(basis) == set(HALLS)
    assert {h: b.design_kw for h, b in basis.items()} == {
        **{h: 1000.0 for h in HALLS[:7]},
        "DH08": 1200.0,
    }
    assert all((b.operating_min, b.operating_max) == (0.55, 0.80) for b in basis.values())
    assert basis["DH08"].liquid_fraction == 0.4


# ---- Weather


def _year(seed: int = 7, step_s: int = 3600, days: int = 365):
    noise = Noise(seed)
    return [(t, outdoor_air(noise, t)) for t in range(START, START + days * DAY, step_s)]


def test_weather_stays_tropical_all_year():
    for t, air in _year():
        assert 24.0 <= air.dry_bulb_c <= 33.0, t
        assert 24.0 <= air.wet_bulb_c <= 27.0, t
        assert 60.0 <= air.rh_pct <= 95.0, t
        assert air.dew_point_c <= air.wet_bulb_c <= air.dry_bulb_c, t
        assert air.rh_pct == pytest.approx(relative_humidity(air.dry_bulb_c, air.dew_point_c))
        assert air.wet_bulb_c == pytest.approx(
            wet_bulb(air.dry_bulb_c, air.dew_point_c, air.pressure_hpa), abs=1e-6
        )
        assert 990 < air.pressure_hpa < 1025 and 0 <= air.wind_mps < 15
        assert 0 <= air.wind_dir_deg < 360 and air.rain_mmph >= 0


def test_weather_has_a_daily_cycle():
    days = _year(days=60)
    afternoon = [a.dry_bulb_c for t, a in days if 13 <= _local_hour(t) < 16]
    dawn = [a.dry_bulb_c for t, a in days if 4 <= _local_hour(t) < 7]
    assert statistics.mean(afternoon) > statistics.mean(dawn) + 4
    assert statistics.mean(a.rh_pct for t, a in days if 4 <= _local_hour(t) < 7) > 85
    assert statistics.mean(a.rh_pct for t, a in days if 13 <= _local_hour(t) < 16) < 75


def test_weather_drifts_deterministically_with_the_season():
    year = _year()
    monthly = [
        statistics.mean(a.wet_bulb_c for _, a in year[m * 730 : (m + 1) * 730]) for m in range(12)
    ]
    assert max(monthly) - min(monthly) > 0.4
    noise = Noise(7)
    assert [outdoor_air(noise, t) for t, _ in year[:48]] == [a for _, a in year[:48]]
    assert _year(seed=8, days=2) != _year(seed=7, days=2)


def test_rain_accumulates_over_the_local_day(plant_design):
    sim = _sim(plant_design)
    wx = sim.state.assets[WEATHER_STATION]
    total, rained = wx["rain_today_mm"], False
    for _ in range(2 * DAY // 60):
        sim.advance(60)
        wx = sim.state.assets[WEATHER_STATION]
        if _local_hour(sim.time) < 1 / 60:
            total = 0.0
        rained |= wx["rain_mmph"] > 0
        assert wx["rain_today_mm"] >= total
        total = wx["rain_today_mm"]
    assert rained


def test_weather_points_project_the_weather_station(plant_design, projector):
    sim = _sim(plant_design)
    sim.advance(10)
    wx = sim.state.assets[WEATHER_STATION]
    p = projector.project(sim.state)
    expected = {
        "Outside Temperature": wx["dry_bulb_c"],
        "Wet Bulb Temperature": wx["wet_bulb_c"],
        "Dew Point": wx["dew_point_c"],
        "Outside Humidity": wx["rh_pct"],
        "Outside Pressure": wx["pressure_hpa"],
        "Wind Speed": wx["wind_mps"],
        "Wind Direction": wx["wind_dir_deg"],
        "Total Precipitation": wx["rain_today_mm"],
    }
    for member, value in expected.items():
        assert p.values[f"{WX}/{member}"] == value, member
        assert projector.coverage.entries[f"{WX}/{member}"].source is PointSource.PLANT_VIEW
    assert p.values[f"{WX}/Station Status"] == "NORMAL"
    assert p.values[f"{WX}/Wind Direction Text"] in {
        "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
        "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
    }  # fmt: skip


def test_high_wet_bulb_raises_the_outdoor_wet_bulb_and_costs_energy(plant_design, projector):
    base, sim = _sim(plant_design), _sim(plant_design)
    sim.schedule(_inject(START, WEATHER_STATION, "weather.high_wet_bulb", severity=0.75))
    for s in (base, sim):
        s.advance(600)
    hot, clean = sim.state.assets[WEATHER_STATION], base.state.assets[WEATHER_STATION]
    assert hot["wet_bulb_c"] == pytest.approx(
        min(clean["wet_bulb_c"] + 0.75 * 4.0, clean["dry_bulb_c"]), abs=1e-6
    )
    assert hot["wet_bulb_c"] > clean["wet_bulb_c"] + 1.0
    assert hot["dry_bulb_c"] == clean["dry_bulb_c"]
    assert hot["dry_bulb_c"] >= hot["wet_bulb_c"] >= hot["dew_point_c"] > clean["dew_point_c"]
    assert hot["rh_pct"] == pytest.approx(relative_humidity(hot["dry_bulb_c"], hot["dew_point_c"]))
    # The towers reject heat to wetter air: the plant works harder, the IT does not change.
    assert sim.state.assets[SITE]["it_kw"] == base.state.assets[SITE]["it_kw"]
    assert (
        sim.state.assets[SITE]["heat_rejection_kw"] > base.state.assets[SITE]["heat_rejection_kw"]
    )
    assert sim.state.assets[SITE]["facility_kw"] > base.state.assets[SITE]["facility_kw"]
    assert (
        projector.project(sim.state).values[f"{DASH}/PUE"]
        > projector.project(base.state).values[f"{DASH}/PUE"]
    )


def test_clearing_high_wet_bulb_lets_the_humid_air_disperse_gradually(plant_design):
    base, sim = _sim(plant_design), _sim(plant_design)
    sim.schedule(_inject(START, WEATHER_STATION, "weather.high_wet_bulb", severity=0.75))
    for s in (base, sim):
        s.advance(600)

    def excess() -> float:
        return (
            sim.state.assets[WEATHER_STATION]["wet_bulb_c"]
            - base.state.assets[WEATHER_STATION]["wet_bulb_c"]
        )

    held = excess()
    assert held > 1.0
    sim.schedule(_clear(sim.time, WEATHER_STATION, "weather.high_wet_bulb"))
    trail = []
    for _ in range(15):
        for s in (base, sim):
            s.advance(120)
        trail.append(excess())
    # No snap back: the humid spell fades through its own dynamics, monotonically.
    assert trail[0] > 0.7 * held
    assert all(a > b > 0.0 for a, b in zip(trail, trail[1:], strict=False))
    for s in (base, sim):
        s.advance(6 * 3600)
    assert abs(excess()) < 1e-3
    assert sim.state.assets[WEATHER_STATION]["dry_bulb_c"] == pytest.approx(
        base.state.assets[WEATHER_STATION]["dry_bulb_c"]
    )


def test_high_wet_bulb_makes_the_air_more_humid_not_hotter():
    noise = Noise(7)
    for t in range(START, START + DAY, 1800):
        air = outdoor_air(noise, t)
        for rise in (0.5, 3.0, 12.0):
            humid = with_wet_bulb_rise(air, rise)
            assert humid.dry_bulb_c == air.dry_bulb_c
            assert humid.wet_bulb_c == pytest.approx(min(air.wet_bulb_c + rise, air.dry_bulb_c))
            assert air.dew_point_c < humid.dew_point_c <= humid.wet_bulb_c + 1e-9
            assert humid.rh_pct <= 100.0 + 1e-9


# ---- IT Load


def test_it_load_follows_the_design_basis_with_a_diurnal_and_weekly_shape(plant_design):
    noise = Noise(7)
    week = range(START, START + 14 * DAY, 600)
    for hall, basis in plant_design.it_basis.items():
        u = {t: it_utilisation(noise, basis, t) for t in week}
        assert all(basis.operating_min <= x <= basis.operating_max for x in u.values()), hall

        def weekday(t: int) -> int:
            return ((t + LOCAL_OFFSET_S) // DAY + 3) % 7  # 0 = Monday

        work = [x for t, x in u.items() if weekday(t) < 5]
        rest = [x for t, x in u.items() if weekday(t) >= 5]
        afternoon = [x for t, x in u.items() if weekday(t) < 5 and 13 <= _local_hour(t) < 17]
        night = [x for t, x in u.items() if weekday(t) < 5 and 2 <= _local_hour(t) < 6]
        assert statistics.mean(work) > statistics.mean(rest) + 0.02, hall
        assert statistics.mean(afternoon) > statistics.mean(night) + 0.04, hall
    basis = plant_design.it_basis
    assert it_utilisation(noise, basis["DH01"], START) != it_utilisation(
        noise, basis["DH02"], START
    )


def test_it_load_is_power_and_all_of_it_becomes_heat_in_its_hall(plant_design):
    sim = _sim(plant_design)
    for _ in range(120):
        sim.step()
        for hall in HALLS:
            it = sim.state.assets[IT[hall]]
            basis = plant_design.it_basis[hall]
            assert it["power_kw"] == pytest.approx(
                basis.design_kw * it_utilisation(sim.noise, basis, sim.time - 1)
            )
            assert sim.state.assets[hall]["it_heat_kw"] == it["power_kw"]


def test_it_energy_integrates_it_power_step_by_step(plant_design):
    sim = _sim(plant_design)
    for _ in range(100):
        before = sim.state.assets[IT["DH01"]]["energy_kwh"]
        sim.step()
        it = sim.state.assets[IT["DH01"]]
        assert it["energy_kwh"] == before + it["power_kw"] / 3600.0


def test_an_it_load_surge_heats_only_its_hall(plant_design):
    base, sim = _sim(plant_design), _sim(plant_design)
    sim.schedule(_inject(START, IT["DH03"], "it.load_surge", severity=1.0))
    for s in (base, sim):
        s.advance(1800)
    surged = sim.state.assets[IT["DH03"]]["power_kw"]
    clean = base.state.assets[IT["DH03"]]["power_kw"]
    assert surged == pytest.approx(min(1000.0, clean + 0.35 * 1000.0))
    assert sim.state.assets["DH03"]["temp_c"] > base.state.assets["DH03"]["temp_c"] + 1
    for hall in HALLS:
        if hall != "DH03":
            assert sim.state.assets[IT[hall]] == base.state.assets[IT[hall]], hall
            # only through the chilled water the plant supplies every hall
            assert sim.state.assets[hall]["temp_c"] == pytest.approx(
                base.state.assets[hall]["temp_c"], abs=0.02
            ), hall


def test_a_surge_never_draws_more_than_the_hall_design(plant_design):
    sim = _sim(plant_design)
    sim.schedule(_inject(START, IT["DH08"], "it.load_surge", severity=1.0))
    sim.schedule(_inject(START, IT["DH01"], "it.load_surge", severity=1.0))
    for _ in range(DAY // 600):
        sim.advance(600)
        assert sim.state.assets[IT["DH08"]]["power_kw"] <= 1200.0
        assert sim.state.assets[IT["DH01"]]["power_kw"] <= 1000.0


def test_hall_it_load_points_project_the_it_equipment(plant_design, projector):
    sim = _sim(plant_design)
    sim.advance(30)
    p = projector.project(sim.state)
    for hall in HALLS:
        power = sim.state.assets[IT[hall]]["power_kw"]
        n = hall[-1]
        em = f"Environment Monitoring/{FLOOR[hall]}/{hall}/IT Load"
        assert p.values[em] == pytest.approx(power, rel=1e-6)
        meters = [p.values[f"BCPM/{n}L{k}/Active Power"] for k in (1, 2, 3)]
        assert sum(meters) == pytest.approx(power, rel=1e-6)


# ---- Site power and KPIs


def test_every_consumer_has_one_load_class_and_a_floor(plant_design):
    sim = _sim(plant_design)
    consumers = [n for n, s in sim.state.assets.items() if "power_kw" in s]
    assert {load_class(plant_design, n) for n in consumers} == set(LoadClass)
    for node in consumers:
        placed = plant_design.assets.get(node)
        room = (
            plant_design.rooms[node] if node in plant_design.rooms else plant_design.room_of(node)
        )
        assert room is not None, node
        assert placed is None or not placed.support
    assert {n for n in consumers if load_class(plant_design, n) is LoadClass.IT} == set(IT.values())


def test_pue_is_facility_over_it_power_from_the_same_step(plant_design, projector):
    sim = _sim(plant_design)
    sim.schedule(_inject(START + 100, IT["DH05"], "it.load_surge"))
    sim.schedule(_inject(START + 200, WEATHER_STATION, "weather.high_wet_bulb"))
    for _ in range(40):
        sim.advance(10)
        state = sim.state
        power = {n: s["power_kw"] for n, s in state.assets.items() if "power_kw" in s}
        it = sum(power[n] for n in IT.values())
        facility = sum(power.values())
        site = state.assets[SITE]
        assert site["it_kw"] == pytest.approx(it, rel=1e-12)
        assert site["facility_kw"] == pytest.approx(facility, rel=1e-12)
        p = projector.project(state)
        assert p.values[f"{DASH}/Total IT Load"] == site["it_kw"]
        assert p.values[f"{DASH}/Total Facility Load"] == site["facility_kw"]
        assert p.values[f"{DASH}/PUE"] == pytest.approx(facility / it, rel=1e-6)
        assert p.values[f"{DASH}/Total Facility Load 2"] == pytest.approx(facility / 1e3, rel=1e-6)
        assert p.values[f"{DASH}/Total IT Load 2"] == pytest.approx(it / 1e3, rel=1e-6)
        assert 1.1 < p.values[f"{DASH}/PUE"] < 1.8


def test_a_surge_moves_it_and_facility_power_in_the_same_step(plant_design, projector):
    base, sim = _sim(plant_design), _sim(plant_design)
    sim.schedule(_inject(START + 5, IT["DH02"], "it.load_surge"))
    for s in (base, sim):
        s.advance(6)
    extra_it = sim.state.assets[SITE]["it_kw"] - base.state.assets[SITE]["it_kw"]
    extra_facility = sim.state.assets[SITE]["facility_kw"] - base.state.assets[SITE]["facility_kw"]
    assert extra_it > 100
    assert extra_facility >= extra_it  # the UPS and transformers lose a share of it too


def _energy(p, *parts: str) -> float:
    return p.values["/".join([f"{DASH}/Energy", *parts])]


def test_energy_by_hall_floor_and_building_sums_consistently(plant_design, projector):
    sim = _sim(plant_design)
    sim.schedule(_inject(START + 600, IT["DH06"], "it.load_surge"))
    facility_kwh = 0.0
    for _ in range(1800):
        sim.step()
        facility_kwh += sim.state.assets[SITE]["facility_kw"] / 3600.0
    p = projector.project(sim.state)

    for floor, halls in (("Level 1", HALLS[:4]), ("Level 2", HALLS[4:])):
        hall_it = [_energy(p, "Floors", floor, "Data Halls", h, "IT Energy") for h in halls]
        for h, e in zip(halls, hall_it, strict=True):
            assert e == sim.state.assets[IT[h]]["energy_kwh"]
        floor_it = _energy(p, "Floors", floor, "Data Halls", "IT Energy")
        assert floor_it == pytest.approx(sum(hall_it), rel=1e-12)
        assert _energy(p, "Floors", floor, "Total Energy") == pytest.approx(
            floor_it + _energy(p, "Floors", floor, "Non-IT Energy"), rel=1e-12
        )
        assert _energy(p, "Floors", floor, "Non-IT Energy") > 0
    floors = [
        _energy(p, "Floors", f, "Total Energy") for f in ("Ground", "Level 1", "Level 2", "Roof")
    ]
    assert all(e > 0 for e in floors)
    total = _energy(p, "Building", "Total Energy")
    assert total == pytest.approx(sum(floors), rel=1e-12)
    assert total == pytest.approx(facility_kwh, rel=1e-9)
    assert p.values[f"{DASH}/Wh_Im"] == total
    assert _energy(p, "Building", "Total IT Energy") == pytest.approx(
        sum(sim.state.assets[IT[h]]["energy_kwh"] for h in HALLS), rel=1e-12
    )
    breakdown = ["Central Cooling", "Heat Rejection", "Ventilation", "Office Lighting", "Others"]
    assert _energy(p, "Building", "Total IT Energy") + sum(
        p.values[f"{DASH}/{b}"] for b in breakdown
    ) == pytest.approx(total, rel=1e-12)
    assert all(p.values[f"{DASH}/{b}"] > 0 for b in breakdown)


def test_plant_kpis_are_derived_from_the_same_state(plant_design, projector):
    sim = _sim(plant_design)
    sim.advance(60)
    site = sim.state.assets[SITE]
    p = projector.project(sim.state)
    rt = 3.51685
    cooling_kw = site["cooling_kw"] + site["heat_rejection_kw"] + site["ventilation_kw"]
    assert p.values[f"{DASH}/Plant Efficiency"] == pytest.approx(
        cooling_kw / (site["cooling_load_kw"] / rt), rel=1e-6
    )
    assert p.values[f"{DASH}/Chilled-water Plant Efficiency"] == pytest.approx(
        site["chw_plant_kw"] / (site["chw_load_kw"] / rt)
    )
    assert 0.3 < p.values[f"{DASH}/Chilled-water Plant Efficiency"] < 1.0
    assert p.values[f"{DASH}/WUE"] == pytest.approx(site["makeup_lph"] / site["it_kw"], rel=1e-6)
    assert 0.3 < p.values[f"{DASH}/WUE"] < 3.0
    assert p.values[f"{DASH}/IT Power Chain Efficiency"] == pytest.approx(
        100 * site["it_kw"] / (site["it_kw"] + site["losses_kw"])
    )
    assert 85 < p.values[f"{DASH}/IT Power Chain Efficiency"] < 99
    assert 90 < p.values[f"{DASH}/Transformer Efficiency"] < 100
    assert 0 < p.values[f"{DASH}/UPS Load Factor"] < 100
    other = "Other"
    assert p.values[f"{other}/IT Load"] == round(site["it_kw"])
    assert p.values[f"{other}/Lighting"] == round(site["lighting_kw"])
    assert p.values[f"{other}/Power Losses"] == round(site["losses_kw"])
    assert p.values[f"{other}/Cooling"] == round(cooling_kw)


def test_rolling_kpis_start_at_the_steady_value_and_follow_slowly(plant_design, projector):
    sim = _sim(plant_design)
    p = projector.project(sim.state)
    pue = p.values[f"{DASH}/PUE"]
    for window in ("Daily", "Monthly", "Annually"):
        assert p.values[f"{DASH}/PUE ({window})"] == pytest.approx(pue, rel=1e-6)
    sim.schedule(_inject(START, WEATHER_STATION, "weather.high_wet_bulb"))
    sim.advance(3600)
    p = projector.project(sim.state)
    now, daily, monthly = (
        p.values[f"{DASH}/PUE"],
        p.values[f"{DASH}/PUE (Daily)"],
        p.values[f"{DASH}/PUE (Monthly)"],
    )
    assert now > daily > monthly >= p.values[f"{DASH}/PUE (Annually)"]
    assert p.values[f"{DASH}/WUE (Daily)"] > p.values[f"{DASH}/WUE (Monthly)"]
    assert p.values[f"{DASH}/Plant Efficiency (Daily)"] > 0


def test_maintenance_falls_due_at_its_schedule(plant_design, projector, asset_model):
    due = asset_model.point("Other/Maintenance Schedule").export_value // 1000
    p = projector.project(_sim(plant_design).state)
    assert p.values["Other/Maintenance Due"] is False
    p = projector.project(_sim(plant_design, start=due).state)
    assert p.values["Other/Maintenance Due"] is True


# ---- Environment Monitoring hall aggregates


def test_hall_aggregates_summarise_that_halls_cold_aisle_sensors(plant_design, projector):
    sim = _sim(plant_design)
    sim.schedule(_inject(START, IT["DH07"], "it.load_surge"))
    sim.advance(900)
    p = projector.project(sim.state)
    for hall in HALLS:
        folder = f"Environment Monitoring/{FLOOR[hall]}/{hall}"
        temps = [p.values[f"{folder}/Environment Monitoring {i}/Temperature"] for i in range(1, 22)]
        rhs = [p.values[f"{folder}/Environment Monitoring {i}/Humidity"] for i in range(1, 22)]
        assert len(set(temps)) > 1, hall  # each sensor sees its own spot
        assert p.values[f"{folder}/Avg Cold Aisle Temp"] == pytest.approx(
            statistics.mean(temps), abs=1e-4
        )
        assert p.values[f"{folder}/Max Cold Aisle Temp"] == pytest.approx(max(temps), abs=1e-4)
        assert p.values[f"{folder}/Avg Cold Aisle Humidity"] == pytest.approx(
            statistics.mean(rhs), abs=1e-4
        )
        assert p.values[f"{folder}/Max Cold Aisle Humidity"] == pytest.approx(max(rhs), abs=1e-4)
        assert 15 < min(temps) and max(temps) < 30 and 30 < min(rhs) and max(rhs) < 80, hall


def test_a_cold_aisle_sensor_reads_its_authored_spot(plant_design):
    spots = cold_aisle_sensors(plant_design)
    by_position: dict[tuple[float, float], set[float]] = {}
    for sensor, (hall, offset) in spots.items():
        a, room = plant_design.asset(sensor), plant_design.room(hall)
        by_position.setdefault((a.x - room.x, a.y - room.y), set()).add(offset)
    # The same spot in any hall runs the same amount warm, whatever the sensor is called,
    # and the aisle warms away from the air-unit (east) wall.
    assert all(len(offsets) == 1 for offsets in by_position.values())
    along = {x: next(iter(o)) for (x, _), o in by_position.items()}
    xs = sorted(along)
    assert all(along[a] > along[b] for a, b in zip(xs, xs[1:], strict=False))
    assert all(abs(o) <= COLD_AISLE_SPREAD_C for _, o in spots.values())


def test_moving_a_cold_aisle_sensor_changes_its_reading(asset_model):
    raw = json.loads(PLANT_DESIGN_PATH.read_text(encoding="utf-8"))
    sensor = "Environment Monitoring/Level 1/DH01/Environment Monitoring 3"
    before = cold_aisle_sensors(parse_plant_design(raw, asset_model))[sensor][1]
    placed = next(a for a in raw["assets"] if a["path"] == sensor)
    placed["x"] = 3
    after = cold_aisle_sensors(parse_plant_design(raw, asset_model))[sensor][1]
    assert after > before


def test_the_p1_kpis_have_a_causal_source(projector, asset_model):
    report = projector.coverage
    for path, entry in report.entries.items():
        point = asset_model.point(path)
        in_scope = (
            path.startswith((f"{WX}/", f"{DASH}/", "Other/"))
            or path.startswith("Environment Monitoring/")
            and point.asset is None
        )
        if not in_scope or point.support:
            continue
        if path.startswith("Other/Gateway"):
            assert entry.source is PointSource.FALLBACK  # the control network is P4 (#25)
        elif point.source_class is SourceClass.STATIC_METADATA:
            continue
        else:
            assert entry.source is not PointSource.FALLBACK, path
    assert not math.isnan(report.debt())
