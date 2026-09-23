"""P1 Data Hall thermal zones and the sensors that observe them (#20)."""

import statistics

import pytest

from graphene_demo_twin.plant_design import ConnectionKind
from graphene_demo_twin.projection import PointSource, Projector
from graphene_demo_twin.sim import Event, Simulation
from graphene_demo_twin.sim.electrical import network
from graphene_demo_twin.sim.placeholder import assets_of
from graphene_demo_twin.sim.thermal import (
    COLD_AISLE_SPREAD_C,
    CRAC_TYPE,
    HOT_AISLE_SPREAD_C,
    ThermalZoneDomain,
    air_suppliers,
    cold_aisle_sensors,
    hot_aisle_sensors,
    supplier_air,
    zones,
)
from graphene_demo_twin.world import default_domains, default_projector

START = 1_790_000_000
HALLS = [f"DH0{i}" for i in range(1, 9)]
SUPPORT_ZONES = ["G-HV", "G-UPSA", "G-UPSB", "G-BAT", "G-NOC", "L1-SUP", "L2-SUP"]
CRAC5 = "CRAC/R_CRAC1"  # the DX unit serving DH05
EM = "Environment Monitoring/Level 2/DH05/Environment Monitoring 8"
TH = "Temperature and Humidity/Datahall 5/Sensor 36"


def _sim(plant_design, seed: int = 7) -> Simulation:
    return Simulation(plant_design, default_domains(), seed, START)


def _inject(at: int, target: str, fault: str, **params) -> Event:
    return Event(at, "fault.inject", target, {"fault": fault, **params})


def _clear(at: int, target: str, fault: str) -> Event:
    return Event(at, "fault.clear", target, {"fault": fault})


def _dh05_loses_cooling(at: int) -> list[Event]:
    """DH05's DX unit trips, leaving only its chilled-water units."""
    return [_inject(at, CRAC5, "crac.compressor_trip"), _inject(at, CRAC5, "crac.fan_failure")]


@pytest.fixture(scope="module")
def projector(asset_model, plant_design) -> Projector:
    return default_projector(asset_model, plant_design)


# ---- Zones


def test_each_data_hall_and_each_support_room_with_air_units_is_its_own_zone(plant_design):
    assert set(zones(plant_design)) == {*HALLS, *SUPPORT_ZONES}
    for zone in zones(plant_design):
        assert air_suppliers(plant_design, zone), zone
    # Rooms without airside units are not zones.
    for room in plant_design.rooms.values():
        if room.id not in zones(plant_design):
            assert not plant_design.upstream(room.id, ConnectionKind.AIR), room.id


def test_zones_start_in_steady_state_with_heat_in_equal_to_cooling_out(plant_design):
    sim = _sim(plant_design)
    for zone in zones(plant_design):
        s = sim.state.assets[zone]
        assert s["heat_kw"] > 0.0, zone
        assert s["cooling_kw"] == pytest.approx(s["heat_kw"], rel=1e-6), zone
        assert s["delivered_fraction"] == pytest.approx(1.0, rel=1e-6), zone
    before = {z: sim.state.assets[z]["temp_c"] for z in zones(plant_design)}
    sim.advance(600)
    for zone in zones(plant_design):  # only the IT Load's noise and daily shape move it
        assert sim.state.assets[zone]["temp_c"] == pytest.approx(before[zone], abs=0.5), zone


def test_crac_units_start_in_steady_state_with_the_zone_they_serve(plant_design):
    # Every CRAC reads its room's return air and loads its compressor for it before the first
    # step, and the zones are balanced against what those units draw: the first step with
    # no event moves nothing an observer could read.
    sim = _sim(plant_design)
    cracs = {c: dict(sim.state.assets[c]) for c in assets_of(plant_design, CRAC_TYPE)}
    temps = {z: sim.state.assets[z]["temp_c"] for z in zones(plant_design)}
    for crac, s in cracs.items():
        room = next(n for n in plant_design.downstream(crac, ConnectionKind.AIR))
        assert s["return_c"] == pytest.approx(temps[room], abs=1e-9), crac
    sim.step()
    for crac, s in cracs.items():
        now = sim.state.assets[crac]
        for key, value in s.items():
            if isinstance(value, float):
                assert now[key] == pytest.approx(value, abs=1e-6), (crac, key)
            else:
                assert now[key] == value, (crac, key)
    for zone, temp in temps.items():
        assert sim.state.assets[zone]["temp_c"] == pytest.approx(temp, abs=1e-6), zone


def test_the_control_ups_heats_the_bms_control_room_with_what_it_carries(plant_design):
    # UPS 25 has no downstream branch: the BMS control room and network load it carries is
    # spent in L1-SUP, as well as its own losses.
    sim = _sim(plant_design)
    sim.advance(30)
    a = sim.state.assets
    ups = a["UPS/UPS 25"]
    assert ups["output_kw"] > 40.0
    circuits = sum(
        a[x.path]["power_kw"]
        for x in plant_design.assets_in("L1-SUP")
        if x.type_id in ("IPS", "RCMS")
    )
    assert a["L1-SUP"]["heat_kw"] == pytest.approx(
        a["L1-SUP"]["power_kw"] + circuits + ups["loss_kw"] + ups["output_kw"], rel=1e-9
    )


def test_heat_in_is_it_load_plus_losses_and_heat_out_is_delivered_cooling(plant_design):
    sim = _sim(plant_design)
    sim.advance(30)
    a = sim.state.assets
    for hall in HALLS:
        it = a[f"~IT-{hall}"]["power_kw"]
        assert a[hall]["it_heat_kw"] == it
        # The hall's own lighting is the only other heat in it.
        assert a[hall]["heat_kw"] == pytest.approx(it + a[hall]["power_kw"], rel=1e-9)
    # The UPS modules' losses heat the UPS rooms, and the transformers' the HV room.
    for room in ("G-UPSA", "G-UPSB"):
        ups = [x.path for x in plant_design.assets_in(room) if x.type_id == "UPS"]
        losses = sum(a[u]["loss_kw"] for u in ups)
        assert losses == pytest.approx(a[room]["ups_heat_kw"]) and losses > 50.0
        assert a[room]["heat_kw"] == pytest.approx(losses + a[room]["power_kw"], rel=1e-9)
    transformers = sum(a[i]["power_kw"] for i in network(plant_design).incomers)
    assert transformers > 10.0
    assert a["G-HV"]["heat_kw"] == pytest.approx(transformers + a["G-HV"]["power_kw"], rel=1e-9)
    # First-order inertia: the temperature moves by the imbalance over the zone's capacity.
    before = {z: dict(a[z]) for z in zones(plant_design)}
    sim.step()
    for zone in zones(plant_design):
        s = sim.state.assets[zone]
        capacity = ThermalZoneDomain.capacity_kj_per_k(plant_design, zone)
        rise = (s["heat_kw"] - s["cooling_kw"]) / capacity
        assert s["temp_c"] - before[zone]["temp_c"] == pytest.approx(rise, abs=1e-9), zone


# ---- Acceptance: losing cooling in DH05 raises only DH05 (v1 #4, #9)


def test_losing_cooling_in_dh05_raises_only_dh05(plant_design, projector):
    base, sim = _sim(plant_design), _sim(plant_design)
    for event in _dh05_loses_cooling(START):
        sim.schedule(event)
    base.advance(1800)
    sim.advance(1800)

    hot, cool = sim.state.assets["DH05"], base.state.assets["DH05"]
    assert hot["temp_c"] > cool["temp_c"] + 5.0
    assert hot["cooling_kw"] < hot["heat_kw"]
    assert hot["delivered_fraction"] < 1.0
    # No zone shares air with DH05 in the Plant Design, so no other hall moves at all. The
    # support rooms see only the power the tripped unit no longer draws: the transformers in
    # the HV room run a little cooler.
    for zone in zones(plant_design):
        if zone in HALLS and zone != "DH05":
            assert sim.state.assets[zone] == base.state.assets[zone], zone
        elif zone != "DH05":
            assert sim.state.assets[zone]["temp_c"] == pytest.approx(
                base.state.assets[zone]["temp_c"], abs=0.1
            ), zone

    p, q = projector.project(sim.state), projector.project(base.state)
    assert p.values["Chiller System Control/Data Halls/DH5 Temperature"] > (
        q.values["Chiller System Control/Data Halls/DH5 Temperature"] + 5.0
    )
    for n in range(1, 9):
        if n != 5:
            view = f"Chiller System Control/Data Halls/DH{n} Temperature"
            assert p.values[view] == q.values[view]
    for sensor, (hall, _) in {
        **cold_aisle_sensors(plant_design),
        **hot_aisle_sensors(plant_design),
    }.items():
        members = ("Temperature", "Humidity") if sensor.startswith("Env") else ("Temp", "Humidity")
        for member in members:
            path = f"{sensor}/{member}"
            if hall == "DH05":
                assert p.values[path] != q.values[path], path
            else:
                assert p.values[path] == q.values[path], path
    # Every hot-aisle and cold-aisle sensor in DH05 warms.
    for sensor, (hall, _) in hot_aisle_sensors(plant_design).items():
        if hall == "DH05":
            assert p.values[f"{sensor}/Temp"] > q.values[f"{sensor}/Temp"] + 5.0


def test_zones_share_heat_only_along_authored_air_connections(plant_design):
    # The Plant Design connects no zone to another, so neighbouring halls are independent.
    zone_set = set(zones(plant_design))
    for c in plant_design.connections:
        assert not (c.source in zone_set and c.target in zone_set), c
    for zone in zones(plant_design):
        for supplier, _ in air_suppliers(plant_design, zone):
            served = [
                n for n in plant_design.downstream(supplier, ConnectionKind.AIR) if n in zone_set
            ]
            assert served == [zone], supplier


def test_a_support_room_loses_cooling_on_its_own(plant_design):
    base, sim = _sim(plant_design), _sim(plant_design)
    sim.schedule(_inject(START, "CRAC/G_CRAC1", "crac.compressor_trip"))
    base.advance(1800)
    sim.advance(1800)
    assert sim.state.assets["G-UPSA"]["temp_c"] > base.state.assets["G-UPSA"]["temp_c"] + 2.0
    for hall in HALLS:
        assert sim.state.assets[hall] == base.state.assets[hall], hall


def test_delivered_fraction_is_the_share_of_demand_met_through_loss_and_recovery(plant_design):
    sim = _sim(plant_design)
    for event in _dh05_loses_cooling(START):
        sim.schedule(event)
    sim.advance(1800)
    hot = sim.state.assets["DH05"]
    assert 0.0 < hot["delivered_fraction"] < 1.0
    assert hot["delivered_fraction"] == pytest.approx(hot["cooling_kw"] / hot["heat_kw"])
    for fault in ("crac.compressor_trip", "crac.fan_failure"):
        sim.schedule(_clear(sim.time, CRAC5, fault))
    for _ in range(600):
        sim.step()
        s = sim.state.assets["DH05"]
        assert 0.0 <= s["delivered_fraction"] <= 1.0
    # Pulling the stored heat back out, the airside delivers more than the hall's demand.
    s = sim.state.assets["DH05"]
    assert s["cooling_kw"] > s["heat_kw"]
    assert s["delivered_fraction"] == 1.0


def test_clear_lets_the_hall_recover_through_its_own_inertia(plant_design):
    sim = _sim(plant_design)
    for event in _dh05_loses_cooling(START):
        sim.schedule(event)
    sim.advance(1800)
    hot = sim.state.assets["DH05"]["temp_c"]
    for fault in ("crac.compressor_trip", "crac.fan_failure"):
        sim.schedule(_clear(sim.time, CRAC5, fault))
    sim.advance(60)
    assert sim.state.assets["DH05"]["temp_c"] > hot - 3.0  # no snap back


# ---- Humidity


def test_a_warmer_hall_holds_the_same_moisture_at_a_lower_relative_humidity(plant_design):
    base, sim = _sim(plant_design), _sim(plant_design)
    for event in _dh05_loses_cooling(START):
        sim.schedule(event)
    base.advance(1800)
    sim.advance(1800)
    hot, cool = sim.state.assets["DH05"], base.state.assets["DH05"]
    assert hot["dew_point_c"] == pytest.approx(cool["dew_point_c"], abs=1e-9)
    assert hot["rh_pct"] < cool["rh_pct"] - 5.0
    for zone in zones(plant_design):
        assert 25.0 < base.state.assets[zone]["rh_pct"] < 75.0, zone


def test_without_its_fresh_air_handler_a_zones_moisture_drifts_towards_outdoors(plant_design):
    base, sim = _sim(plant_design), _sim(plant_design)
    # The breaker feeding DH05's fresh-air handler trips; nothing else it feeds is a zone's
    # dehumidifier.
    pahu = "PAHU/R_PAHU1"
    feeder = next(
        n
        for n in plant_design.upstream(pahu, ConnectionKind.POWER, transitive=True)
        if plant_design.assets.get(n) is not None and plant_design.asset(n).type_id == "GPM96"
    )
    sim.schedule(_inject(START, feeder, "gpm96.breaker_trip"))
    base.advance(3600)
    sim.advance(3600)
    assert sim.state.assets["DH05"]["dew_point_c"] > base.state.assets["DH05"]["dew_point_c"] + 2


# ---- Sensors


def test_cold_aisle_and_hot_aisle_sensors_observe_their_zone_with_bounded_spread(
    plant_design, projector
):
    sim = _sim(plant_design)
    sim.advance(120)
    p = projector.project(sim.state)
    for hall in HALLS:
        zone = sim.state.assets[hall]
        cold = [
            p.values[f"{s}/Temperature"]
            for s, (h, _) in cold_aisle_sensors(plant_design).items()
            if h == hall
        ]
        hot = [
            p.values[f"{s}/Temp"]
            for s, (h, _) in hot_aisle_sensors(plant_design).items()
            if h == hall
        ]
        assert len(cold) == 21 and len(hot) == 8, hall
        assert len(set(cold)) > 1 and len(set(hot)) > 1, hall  # each sees its own spot
        assert all(abs(t - zone["cold_aisle_c"]) <= COLD_AISLE_SPREAD_C + 1e-4 for t in cold)
        assert all(abs(t - zone["temp_c"]) <= HOT_AISLE_SPREAD_C + 1e-4 for t in hot)
        assert min(hot) > max(cold), hall  # return air is warmer than supply
        view = f"Chiller System Control/Data Halls/DH{hall[-1]} Temperature"
        assert p.values[view] == pytest.approx(zone["temp_c"], rel=1e-9)
        assert projector.coverage.entries[view].source is PointSource.PLANT_VIEW
    halls_heat = sum(sim.state.assets[h]["heat_kw"] for h in HALLS)
    assert p.values["Chiller System Control/Data Halls/Current Heat Load"] == pytest.approx(
        halls_heat, rel=1e-9
    )


def test_less_airflow_recirculates_more_hot_aisle_air_into_the_cold_aisle(plant_design, projector):
    base, sim = _sim(plant_design), _sim(plant_design)
    sim.schedule(_inject(START, CRAC5, "crac.fan_failure", severity=0.5))
    base.advance(1800)
    sim.advance(1800)
    assert sim.state.assets[CRAC5]["running"]  # a weaker fan, not a trip

    def recirculated(state) -> float:
        """The share of the cold aisle's air that came back from the hot aisle."""
        flow = supplied = 0.0
        for supplier, share in air_suppliers(plant_design, "DH05"):
            airflow, supply_c = supplier_air(state, plant_design, supplier)
            flow += share * airflow
            supplied += share * airflow * supply_c
        mixed = supplied / flow
        zone = state.assets["DH05"]
        return (zone["cold_aisle_c"] - mixed) / (zone["temp_c"] - mixed)

    assert recirculated(sim.state) > recirculated(base.state) + 0.05
    p, q = projector.project(sim.state), projector.project(base.state)
    for sensor, (hall, _) in cold_aisle_sensors(plant_design).items():
        if hall == "DH05":
            path = f"{sensor}/Temperature"
            assert p.values[path] > q.values[path] + 0.5, path


def test_current_heat_load_is_the_heat_the_halls_generate_not_the_cooling_they_get(
    plant_design, projector
):
    view = "Chiller System Control/Data Halls/Current Heat Load"
    base, outage, surge = _sim(plant_design), _sim(plant_design), _sim(plant_design)
    for event in _dh05_loses_cooling(START):
        outage.schedule(event)
    surge.schedule(_inject(START, "~IT-DH04", "it.load_surge"))
    for s in (base, outage, surge):
        s.advance(1800)
    p, q, r = (projector.project(s.state).values for s in (outage, base, surge))
    halls_heat = sum(outage.state.assets[h]["heat_kw"] for h in HALLS)
    halls_cooling = sum(outage.state.assets[h]["cooling_kw"] for h in HALLS)
    assert halls_cooling < halls_heat - 50.0
    assert p[view] == pytest.approx(halls_heat, rel=1e-9)
    assert p[view] == pytest.approx(q[view], rel=1e-9)  # the tripped unit generates no heat
    assert r[view] > q[view] + 100.0


@pytest.mark.parametrize(
    ("sensor", "member", "prefix"),
    [(TH, "Temp", "th"), (EM, "Temperature", "em")],
)
@pytest.mark.parametrize("kind", ["offset", "drift", "stuck"])
def test_a_sensor_fault_changes_only_the_observation(
    plant_design, projector, sensor, member, prefix, kind
):
    base, sim = _sim(plant_design), _sim(plant_design)
    for s in (base, sim):  # the hall warms, so a stuck reading falls behind it
        for event in _dh05_loses_cooling(START + 60):
            s.schedule(event)
    sim.schedule(_inject(START, sensor, f"{prefix}.{kind}", severity=1.0))
    base.advance(3600)
    sim.advance(3600)

    # The world is unchanged: every zone, and every other sensor, reads as without the fault.
    # Offset and drift corrupt only the temperature element; a stuck sensor freezes both
    # readings. Only an Environment Monitoring sensor feeds its hall's cold-aisle aggregates.
    for zone in zones(plant_design):
        assert sim.state.assets[zone] == base.state.assets[zone], zone
    p, q = projector.project(sim.state), projector.project(base.state)
    changed = {path for path in q.values if p.values[path] != q.values[path]}
    folder = "Environment Monitoring/Level 2/DH05/"
    allowed = {f"{sensor}/{member}"}
    if prefix == "em":
        allowed |= {f"{folder}Avg Cold Aisle Temp", f"{folder}Max Cold Aisle Temp"}
    if kind == "stuck":
        allowed.add(f"{sensor}/Humidity")
        if prefix == "em":
            allowed |= {f"{folder}Avg Cold Aisle Humidity", f"{folder}Max Cold Aisle Humidity"}
    assert f"{sensor}/{member}" in changed
    assert changed <= allowed
    assert f"{folder}Avg Cold Aisle Temp" in changed or prefix == "th"

    # So the reading disagrees with the zone state and with its neighbours, which agree.
    reading, truth = p.values[f"{sensor}/{member}"], q.values[f"{sensor}/{member}"]
    assert abs(reading - truth) > 2.0
    peers = [
        p.values[f"{s}/{member}"]
        for s, (h, _) in {
            **cold_aisle_sensors(plant_design),
            **hot_aisle_sensors(plant_design),
        }.items()
        if h == "DH05" and s != sensor and s.split("/")[0] == sensor.split("/")[0]
    ]
    spread = HOT_AISLE_SPREAD_C if prefix == "th" else COLD_AISLE_SPREAD_C
    assert abs(reading - statistics.median(peers)) > 2.0 * spread
    assert max(peers) - min(peers) <= 2.0 * spread + 1e-4


def test_a_drifting_sensor_drifts_further_over_time_and_clear_recalibrates_it(
    plant_design, projector
):
    sim, base = _sim(plant_design), _sim(plant_design)
    sim.schedule(_inject(START, TH, "th.drift", severity=0.5))
    errors = []
    for _ in range(3):
        sim.advance(1200)
        base.advance(1200)
        errors.append(
            projector.project(sim.state).values[f"{TH}/Temp"]
            - projector.project(base.state).values[f"{TH}/Temp"]
        )
    assert 0.0 < errors[0] < errors[1] < errors[2]
    sim.schedule(_clear(sim.time, TH, "th.drift"))
    sim.advance(1)
    base.advance(1)
    assert projector.project(sim.state).values[f"{TH}/Temp"] == pytest.approx(
        projector.project(base.state).values[f"{TH}/Temp"], abs=1e-9
    )


def test_a_stuck_sensor_holds_both_readings(plant_design, projector):
    sim = _sim(plant_design)
    sim.advance(10)
    held = projector.project(sim.state)
    sim.schedule(_inject(sim.time, EM, "em.stuck"))
    for event in _dh05_loses_cooling(sim.time):
        sim.schedule(event)
    sim.advance(900)
    p = projector.project(sim.state)
    for member in ("Temperature", "Humidity"):
        assert p.values[f"{EM}/{member}"] == pytest.approx(held.values[f"{EM}/{member}"], abs=0.05)


def test_a_recharging_ups_heats_its_room_only_by_its_losses(plant_design):
    sim = _sim(plant_design)
    sim.schedule(_inject(START, "UPS/UPS 1", "ups.rectifier_failure"))
    sim.schedule(_clear(START + 600, "UPS/UPS 1", "ups.rectifier_failure"))
    sim.advance(660)
    a = sim.state.assets
    assert a["UPS/UPS 1"]["battery_kw"] > 1.0  # the battery takes charge, and stores it
    assert a["G-UPSA"]["heat_kw"] == pytest.approx(
        a["G-UPSA"]["ups_heat_kw"] + a["G-UPSA"]["power_kw"], rel=1e-9
    )
