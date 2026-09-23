"""P1 electrical network and power flow (#19): sources, 2N MSBs with ATS and gensets, hall UPS
in distributed redundancy, branch circuits, fuel, and a meter tree that always balances."""

import math

import pytest

from graphene_demo_twin.asset_model import SourceClass
from graphene_demo_twin.faults import STANDARD_CATALOG
from graphene_demo_twin.projection import PointSource, Projector, Quality
from graphene_demo_twin.projection.electrical import UNMODELLED
from graphene_demo_twin.sim import Event, Simulation
from graphene_demo_twin.sim.electrical import (
    GENSET_KW,
    OVERLOAD_TRIP_S,
    TRANSFER_LIMIT_S,
    network,
    powered,
)
from graphene_demo_twin.sim.site import SITE, consumers
from graphene_demo_twin.sim.thermal import air_suppliers, supplier_air
from graphene_demo_twin.world import default_domains, default_projector

START = 1_790_000_000
INC = {i: f"Meter/SPPA Incomer {i}" for i in range(1, 5)}
BUS_A, BUS_B = "Meter/Level 2_MSB A_1", "Meter/Level 2_MSB B_9"
DB = "Meter/Level 1_DB_18"
GEN = {g: f"Genset/Genset {g}" for g in range(1, 7)}
SIDE_A_UPS = [1, 3, 4, 7, 9, 10, 13, 15, 16, 19, 21, 22]
SIDE_B_UPS = [2, 5, 6, 8, 11, 12, 14, 17, 18, 20, 23, 24]
HALLS = [f"DH0{i}" for i in range(1, 9)]


def _sim(plant_design, seed: int = 7) -> Simulation:
    return Simulation(plant_design, default_domains(), seed, START)


def _inject(at: int, target: str, fault: str, **params) -> Event:
    return Event(at, "fault.inject", target, {"fault": fault, **params})


def _clear(at: int, target: str, fault: str) -> Event:
    return Event(at, "fault.clear", target, {"fault": fault})


def _ups(n: int) -> str:
    return f"UPS/UPS {n}"


def _hall_ups(hall: str) -> list[str]:
    h = int(hall[-1])
    return [_ups(3 * (h - 1) + k) for k in (1, 2, 3)]


def _branches(hall: str) -> list[str]:
    return [f"BCPM/{hall[-1]}L{k}" for k in (1, 2, 3)]


@pytest.fixture(scope="module")
def projector(asset_model, plant_design) -> Projector:
    return default_projector(asset_model, plant_design)


# ---- The authored power graph


def test_every_consumer_hangs_off_exactly_one_board(plant_design):
    for node in consumers(plant_design):
        suppliers = plant_design.upstream(node, "power")
        if plant_design.assets.get(node) and plant_design.asset(node).type_id == "IT Load":
            assert len(suppliers) == 3, node  # three UPS branches, distributed redundant
        elif node in network(plant_design).incomers:
            assert suppliers == (), node  # the grid is upstream of the site
        else:
            assert len(suppliers) == 1, (node, suppliers)


def test_every_board_traces_back_to_both_kinds_of_source(plant_design):
    net = network(plant_design)
    assert net.incomers == tuple(INC.values())
    assert [b.node for b in net.buses] == [BUS_A, BUS_B]
    assert net.buses[0].incomers == (INC[1], INC[2]) and net.buses[1].incomers == (INC[3], INC[4])
    assert net.buses[0].gensets == (GEN[1], GEN[2], GEN[3])
    assert net.buses[1].gensets == (GEN[4], GEN[5], GEN[6])
    for node in net.order:
        up = set(plant_design.upstream(node, "power", transitive=True))
        assert up & set(net.incomers) and up & set(GEN.values()), node


# ---- Steady state


def test_the_base_world_runs_on_utility_with_full_batteries(plant_design, projector):
    sim = _sim(plant_design)
    sim.advance(120)
    a = sim.state.assets
    assert a[BUS_A]["source"] == "utility" and a[BUS_B]["source"] == "utility"
    assert a[DB]["source"] == BUS_A
    for n in range(1, 26):
        assert a[_ups(n)]["mode"] == "online" and a[_ups(n)]["soc"] == 1.0, n
    for g in GEN.values():
        assert a[g]["stage"] == "standby" and a[g]["p_kw"] == 0.0
    p = projector.project(sim.state)
    assert p.values[f"{BUS_A}/Hz"] == pytest.approx(50.0, abs=0.1)
    assert 225 < p.values[f"{BUS_A}/V1"] < 240 and 390 < p.values[f"{BUS_A}/V12"] < 415
    assert p.values[f"{INC[1]}/V1"] == pytest.approx(22000 / math.sqrt(3), rel=0.03)
    for h in HALLS:
        load = a[f"~IT-{h}"]["power_kw"]
        for b in _branches(h):
            assert p.values[f"{b}/Active Power"] == pytest.approx(load / 3, rel=1e-5)
    assert not any(v for path, v in p.values.items() if path.endswith("/HasAlarm") and _elec(path))


def _elec(path: str) -> bool:
    return path.startswith(("Meter/Level", "Meter/SPPA", "Meter/Meter", "UPS/", "Genset/", "BCPM/"))


# ---- The meter tree balances at every step


def _flow_in(a, node: str, parent: str) -> tuple[float, float | None]:
    """What `node` draws from `parent`: (kW, kvar), with kvar None for a load, whose reactive
    power only the model knows."""
    s = a[node]
    if "fed_by" in s:  # a transfer board: it draws only from the side that fed it
        return (s["p_kw"], s["q_kvar"]) if s["fed_by"] == parent else (0.0, 0.0)
    if "p_kw" in s:
        return s["p_kw"], s["q_kvar"]
    return s["power_kw"], None


def assert_balanced(state, design) -> None:
    a = state.assets
    net = network(design)
    for m in net.order:
        p, q, exact_q = 0.0, 0.0, True
        for child in design.downstream(m, "power"):
            cp, cq = _flow_in(a, child, m)
            p += cp
            if cq is None:
                exact_q = False
            else:
                q += cq
        if "power_kw" in a[m]:
            p += a[m]["power_kw"]  # a stand-in draw beyond the meter (lifts, genset auxiliaries)
            exact_q = False
        assert a[m]["p_kw"] == pytest.approx(p, rel=1e-9, abs=1e-9), m
        if exact_q:
            assert a[m]["q_kvar"] == pytest.approx(q, rel=1e-9, abs=1e-9), m
    for bus in net.buses:
        supplied = sum(a[i]["p_kw"] - a[i]["power_kw"] for i in bus.incomers)
        generated = sum(a[g]["p_kw"] for g in bus.gensets)
        if a[bus.node]["fed_by"] == "utility":  # the source during the step, not after it
            assert generated == 0.0
        assert supplied + generated == pytest.approx(a[bus.node]["p_kw"], rel=1e-9, abs=1e-6)
    for n in range(1, 26):
        u = a[_ups(n)]
        # battery_kw charges (+) or discharges (-) the battery
        assert u["p_kw"] == pytest.approx(
            u["output_kw"] + u["loss_kw"] + u["battery_kw"], abs=1e-9
        ), n
        # A hall UPS dissipates its losses; the control UPS also consumes its BMS load.
        assert u["loss_kw"] >= 0.0 and u["output_kw"] >= 0.0
        assert u["power_kw"] == pytest.approx(
            u["loss_kw"] + (u["output_kw"] if n == 25 else 0.0), abs=1e-9
        )
    for h in HALLS:
        branches = [a[b]["p_kw"] for b in _branches(h)]
        assert sum(branches) == pytest.approx(a[f"~IT-{h}"]["power_kw"], rel=1e-12, abs=1e-9)
        for ups, b in zip(_hall_ups(h), _branches(h), strict=True):
            assert a[ups]["output_kw"] == a[b]["p_kw"]
    sources = sum(a[i]["p_kw"] for i in net.incomers) + sum(a[g]["p_kw"] for g in net.gensets)
    drawn = sum(a[n]["power_kw"] for n in consumers(design))
    stored = sum(a[_ups(n)]["battery_kw"] for n in range(1, 26))
    assert all(a[n]["power_kw"] >= 0.0 for n in consumers(design))
    assert a[SITE]["storage_kw"] == pytest.approx(stored, rel=1e-9, abs=1e-9)
    assert sources == pytest.approx(drawn + stored, rel=1e-9, abs=1e-6)  # batteries: sink/source
    assert a[SITE]["facility_kw"] == pytest.approx(drawn, rel=1e-9)


def assert_phases_balanced(p, state, design) -> None:
    """Per phase and in total, every board whose children are all meters is the sum of them;
    every meter's derived quantities agree with each other."""
    a = state.assets
    net = network(design)
    for m in net.order:
        three = f"{m}/P2" in p.values
        phases = (1, 2, 3) if three else (1,)
        total = sum(p.values[f"{m}/P{k}"] for k in phases)
        assert p.values[f"{m}/Ptot"] == pytest.approx(total, rel=1e-5, abs=1e-3), m
        assert p.values[f"{m}/Ptot"] == pytest.approx(a[m]["p_kw"], rel=1e-5, abs=1e-3), m
        stot = 0.0
        for k in phases:
            pk, qk, sk = (p.values[f"{m}/{x}{k}"] for x in "PQS")
            assert sk == pytest.approx(math.hypot(pk, qk), rel=1e-5, abs=1e-3), (m, k)
            if sk > 1e-3:
                assert p.values[f"{m}/PF{k}"] == pytest.approx(pk / sk, rel=1e-5), (m, k)
            if sk > 1e-3:  # power flowed, so the meter had voltage while it did
                assert p.values[f"{m}/V{k}"] > 0, (m, k)
                amps = sk * 1000 / p.values[f"{m}/V{k}"]
                assert p.values[f"{m}/I{k}"] == pytest.approx(amps, rel=1e-4), (m, k)
            elif p.values[f"{m}/V{k}"] == 0:
                assert p.values[f"{m}/I{k}"] == 0, (m, k)
            stot += sk
        assert p.values[f"{m}/Stot"] == pytest.approx(stot, rel=1e-5, abs=1e-3), m
        assert p.values[f"{m}/Wh_Im"] == pytest.approx(a[m]["energy_kwh"], rel=1e-6, abs=1e-3)
        children = design.downstream(m, "power")
        if not children or not all(c in net.meters for c in children) or "power_kw" in a[m]:
            continue
        for k in phases:
            for x in "PQ":
                below = 0.0
                for c in children:
                    if a[c].get("fed_by", m) != m:
                        continue  # the transfer board was fed from the other side
                    if f"{c}/P2" in p.values:
                        below += p.values[f"{c}/{x}{k}"] if three else p.values[f"{c}/{x}tot"]
                    elif k == 1:  # a single-phase board sits on phase 1
                        below += p.values[f"{c}/{x}1"]
                assert p.values[f"{m}/{x}{k}"] == pytest.approx(below, rel=1e-5, abs=1e-2), (
                    m,
                    x,
                    k,
                )


def test_the_meter_tree_balances_at_every_step(plant_design, projector):
    sim = _sim(plant_design)
    t = START + 30
    for event in (
        _inject(t, INC[1], "utility.incomer_loss"),
        _inject(t, INC[2], "utility.incomer_loss"),
        _inject(t + 60, "BCPM/2L2", "bcpm.breaker_trip"),
        _inject(t + 90, _ups(13), "ups.rectifier_failure"),
        _inject(t + 120, "Meter/Level 2_MSB B_16", "gpqm144.breaker_trip"),
        _inject(t + 150, "~IT-DH04", "it.load_surge"),
        _clear(t + 240, INC[1], "utility.incomer_loss"),
        _clear(t + 240, INC[2], "utility.incomer_loss"),
        _clear(t + 300, "Meter/Level 2_MSB B_16", "gpqm144.breaker_trip"),
    ):
        sim.schedule(event)
    energy = {m: 0.0 for m in network(plant_design).order}
    for i in range(900):
        sim.step()
        assert_balanced(sim.state, plant_design)
        for m in energy:
            energy[m] += sim.state.assets[m]["p_kw"] / 3600
        if i % 30 == 0:
            assert_phases_balanced(projector.project(sim.state), sim.state, plant_design)
    for m, kwh in energy.items():
        assert sim.state.assets[m]["energy_kwh"] == pytest.approx(kwh, rel=1e-9, abs=1e-9), m


# ---- Utility loss, ATS and gensets


def test_losing_both_side_a_incomers_transfers_to_the_genset_bus_within_15_s(
    plant_design, projector
):
    sim = _sim(plant_design)
    t0 = START + 60
    sim.schedule(_inject(t0, INC[1], "utility.incomer_loss"))
    sim.schedule(_inject(t0, INC[2], "utility.incomer_loss"))
    sim.run_until(t0)
    a = sim.state.assets
    it_before = {h: a[f"~IT-{h}"]["power_kw"] for h in HALLS}

    transferred = None
    socs = {n: [] for n in SIDE_A_UPS}
    hz, volts = [], []
    while sim.time < t0 + 60:
        sim.step()
        a = sim.state.assets
        if transferred is None and a[BUS_A]["live"]:
            transferred = sim.time
            assert a[BUS_A]["source"] == "genset"
        for n in SIDE_A_UPS:
            socs[n].append(a[_ups(n)]["soc"])
        for n in SIDE_B_UPS:
            assert a[_ups(n)]["mode"] == "online" and a[_ups(n)]["soc"] == 1.0
        assert a[BUS_B]["source"] == "utility" and a[BUS_B]["live"]
        for h in HALLS:  # the UPS bridge the gap: no hall loses its IT
            assert powered(sim.state, plant_design, f"~IT-{h}"), h
            assert a[f"~IT-{h}"]["power_kw"] == pytest.approx(it_before[h], rel=0.1)
        if transferred:
            hz.append(a[GEN[1]]["hz"])
            volts.append(a[GEN[1]]["v_pu"])

    assert transferred is not None and transferred - t0 <= TRANSFER_LIMIT_S == 15
    for n in SIDE_A_UPS:
        assert min(socs[n]) < 0.999, n  # the battery carried the hall through the gap
    assert min(hz) < 49.0 and hz[-1] == pytest.approx(50.0, abs=0.3)  # dip on the block load
    assert min(volts) < 0.95 and volts[-1] == pytest.approx(1.0, abs=0.03)

    a = sim.state.assets
    online = [g for g in (GEN[1], GEN[2], GEN[3]) if a[g]["online"]]
    assert len(online) == 3
    assert sum(a[g]["p_kw"] for g in online) == pytest.approx(a[BUS_A]["p_kw"], rel=1e-9)
    assert all(a[g]["stage"] == "standby" for g in (GEN[4], GEN[5], GEN[6]))
    p = projector.project(sim.state)
    for g in online:
        load = a[g]["p_kw"] / GENSET_KW
        assert 0.3 < load < 1.0
        assert p.values[f"{g}/Run Command Active"] == 1
        assert p.values[f"{g}/Engine Speed"] == pytest.approx(p.values[f"{g}/Frequency"] * 30)
        assert p.values[f"{g}/Frequency"] < 50.0 + 0.25 * (1 - 2 * load) + 0.05
        assert 220 < p.values[f"{g}/AC Voltage: L1-N"] < 240
        assert p.values[f"{g}/Oil Pressure"] > 3
    for n in SIDE_A_UPS:
        assert p.values[f"{_ups(n)}/Power Supply Failure"] is False  # back on genset power
        assert p.values[f"{_ups(n)}/Average Input Voltage"] > 380

    # The batteries recharge on genset power.
    sim.advance(900)
    for n in SIDE_A_UPS:
        assert sim.state.assets[_ups(n)]["soc"] > 0.999, n

    # More load on the genset bus: frequency and voltage droop, the engines run warmer.
    loaded = sim.fork()
    loaded.schedule(_inject(loaded.time, "~IT-DH01", "it.load_surge"))
    loaded.schedule(_inject(loaded.time, "~IT-DH03", "it.load_surge"))
    for s in (sim, loaded):
        s.advance(600)
    light, heavy = sim.state.assets[GEN[1]], loaded.state.assets[GEN[1]]
    assert heavy["p_kw"] > light["p_kw"] + 100
    assert heavy["hz"] < light["hz"] and heavy["v_pu"] < light["v_pu"]
    assert heavy["coolant_c"] > light["coolant_c"]

    # Utility returns: retransfer after the delay, gensets cool down unloaded and stop.
    for i in (1, 2):
        sim.schedule(_clear(sim.time, INC[i], "utility.incomer_loss"))
    sim.advance(200)
    a = sim.state.assets
    assert a[BUS_A]["source"] == "utility"
    assert all(a[g]["stage"] == "cooldown" and a[g]["p_kw"] == 0.0 for g in online)
    sim.advance(400)
    assert all(sim.state.assets[g]["stage"] == "standby" for g in online)


def test_one_incomer_lost_leaves_the_other_transformer_carrying_the_side(plant_design):
    sim = _sim(plant_design)
    sim.schedule(_inject(START + 10, INC[1], "utility.incomer_loss"))
    sim.advance(30)
    a = sim.state.assets
    assert a[BUS_A]["source"] == "utility" and a[BUS_A]["live"]
    assert a[INC[1]]["p_kw"] == 0.0 and not a[INC[1]]["live"]
    assert a[INC[2]]["p_kw"] - a[INC[2]]["power_kw"] == pytest.approx(a[BUS_A]["p_kw"])
    assert all(a[g]["stage"] == "standby" for g in GEN.values())
    assert all(a[_ups(n)]["mode"] == "online" for n in SIDE_A_UPS)


def test_a_genset_that_fails_to_start_leaves_n_plus_1_to_carry_the_bus(plant_design, projector):
    sim = _sim(plant_design)
    t0 = START + 60
    sim.schedule(_inject(START, GEN[2], "genset.fail_to_start"))
    for i in (1, 2):
        sim.schedule(_inject(t0, INC[i], "utility.incomer_loss"))
    sim.run_until(t0)
    while sim.state.assets[BUS_A]["source"] != "genset":
        sim.step()
    assert sim.time - t0 <= TRANSFER_LIMIT_S
    # N carry the side within their rating through the block load and the UPS recharge.
    charged = False
    for _ in range(1800):
        sim.step()
        a = sim.state.assets
        assert [g for g in (GEN[1], GEN[2], GEN[3]) if a[g]["online"]] == [GEN[1], GEN[3]]
        for g in (GEN[1], GEN[3]):
            assert a[g]["load_pct"] <= 100.0, (sim.time, a[g]["p_kw"])
            assert not a[g]["overload"]
        charged |= a[SITE]["storage_kw"] > 100.0
    assert charged
    assert all(a[_ups(n)]["soc"] > 0.999 for n in SIDE_A_UPS)
    assert a[GEN[2]]["stage"] == "failed" and a[GEN[2]]["p_kw"] == 0.0
    p = projector.project(sim.state)
    assert p.values[f"{GEN[2]}/Over Crank Shutdown"] is True
    assert p.values[f"{GEN[2]}/General Genset Alarm"] is True
    assert p.values[f"{GEN[2]}/HasAlarm"] is True
    assert p.values[f"{GEN[1]}/Over Crank Shutdown"] is False


def test_an_overloaded_genset_trips_rather_than_run_beyond_its_rating(plant_design, projector):
    sim = _sim(plant_design)
    t0 = START + 60
    sim.schedule(_inject(START, GEN[2], "genset.fail_to_start"))
    for i in (1, 2):
        sim.schedule(_inject(t0, INC[i], "utility.incomer_loss"))
    for h in HALLS:  # far more IT Load than two sets can carry
        sim.schedule(_inject(t0 + 120, f"~IT-{h}", "it.load_surge", severity=1.0))
    over = dict.fromkeys((GEN[1], GEN[3]), 0)
    for _ in range(300):
        sim.step()
        for g in over:
            s = sim.state.assets[g]
            over[g] = over[g] + 1 if s["load_pct"] > 100.0 else 0
            assert over[g] <= OVERLOAD_TRIP_S, g
    a = sim.state.assets
    assert all(a[g]["stage"] == "failed" and a[g]["overload_trip"] for g in over)
    assert not a[BUS_A]["live"] and all(a[_ups(n)]["mode"] == "battery" for n in SIDE_A_UPS)
    p = projector.project(sim.state)
    assert p.values[f"{GEN[1]}/General Genset Alarm"] is True
    # With no genset to hold the bus, the ATS returns it to the utility at once; the trip
    # resets once the start command drops.
    for i in (1, 2):
        sim.schedule(_clear(sim.time, INC[i], "utility.incomer_loss"))
    sim.advance(5)
    assert sim.state.assets[BUS_A]["source"] == "utility"
    assert all(sim.state.assets[g]["stage"] == "standby" for g in over)


def test_a_failed_ats_leaves_the_bus_dead_until_cleared(plant_design, projector):
    sim = _sim(plant_design)
    t0 = START + 60
    sim.schedule(_inject(START, BUS_A, "ats.fail_to_transfer"))
    for i in (1, 2):
        sim.schedule(_inject(t0, INC[i], "utility.incomer_loss"))
    sim.run_until(t0 + 120)
    a = sim.state.assets
    assert not a[BUS_A]["live"] and a[BUS_A]["source"] is None
    assert all(a[g]["stage"] == "running" and not a[g]["online"] for g in (GEN[1], GEN[3]))
    assert all(a[_ups(n)]["mode"] == "battery" for n in SIDE_A_UPS)
    soc = a[_ups(1)]["soc"]
    assert soc < 0.95
    p = projector.project(sim.state)
    assert p.values[f"{BUS_A}/HasAlarm"] is True and p.values[f"{BUS_A}/V1"] == 0.0
    assert p.values[f"{DB}/V1"] > 200  # the L1 DB changed over to MSB B
    assert sim.state.assets[DB]["source"] == BUS_B
    sim.schedule(_clear(sim.time, BUS_A, "ats.fail_to_transfer"))
    sim.advance(3)
    assert sim.state.assets[BUS_A]["live"] and sim.state.assets[BUS_A]["source"] == "genset"


def test_a_degraded_battery_bridges_the_same_gap_with_more_of_its_charge(plant_design):
    healthy, degraded = _sim(plant_design), _sim(plant_design)
    degraded.schedule(_inject(START, _ups(1), "ups.battery_degradation", severity=0.5))
    lowest = {}
    for sim in (healthy, degraded):
        for i in (1, 2):
            sim.schedule(_inject(START + 30, INC[i], "utility.incomer_loss"))
        socs = []
        for _ in range(60):
            sim.step()
            socs.append(sim.state.assets[_ups(1)]["soc"])
        lowest[sim] = min(socs)
    drop_h, drop_d = 1.0 - lowest[healthy], 1.0 - lowest[degraded]
    assert drop_h > 0.005
    assert drop_d == pytest.approx(drop_h / (1 - 0.4), rel=0.02)  # 40 % of capacity gone


# ---- UPS in distributed redundancy


def _fail_first_ups(sim, hall: str) -> None:
    """Fail the hall's first UPS module and run until its battery is exhausted."""
    failed = _hall_ups(hall)[0]
    sim.schedule(_inject(sim.time, failed, "ups.rectifier_failure"))
    while sim.state.assets[failed]["mode"] != "off":
        sim.step()
        assert sim.time < START + 7200
    sim.advance(5)


@pytest.mark.parametrize("hall", HALLS)
def test_one_ups_failing_moves_its_share_onto_the_other_two(plant_design, projector, hall):
    sim = _sim(plant_design)
    failed, *others = _hall_ups(hall)
    lost, *kept = _branches(hall)
    before = projector.project(sim.state)
    sim.schedule(_inject(START, failed, "ups.rectifier_failure"))
    sim.advance(60)
    a = sim.state.assets
    assert a[failed]["mode"] == "battery" and a[failed]["p_kw"] == 0.0
    assert a[lost]["p_kw"] > 0  # still carried from its battery
    p = projector.project(sim.state)
    assert p.values[f"{failed}/Rectifier Failure"] is True
    assert p.values[f"{failed}/HasAlarm"] is True

    while sim.state.assets[failed]["mode"] != "off":
        sim.step()
        assert sim.time < START + 3600
    sim.advance(5)
    a = sim.state.assets
    it = a[f"~IT-{hall}"]["power_kw"]
    assert a[lost]["p_kw"] == 0.0
    for ups, branch in zip(others, kept, strict=True):
        assert a[branch]["p_kw"] == pytest.approx(it / 2)
        assert a[ups]["load_pct"] <= 75.0
        assert a[ups]["mode"] == "online"
    p = projector.project(sim.state)
    assert p.values[f"{lost}/Current"] == 0.0 and p.values[f"{lost}/Active Power"] == 0.0
    for branch in kept:
        ratio = p.values[f"{branch}/Current"] / before.values[f"{branch}/Current"]
        load_ratio = (it / 2) / before.values[f"{branch}/Active Power"]
        assert ratio == pytest.approx(load_ratio, rel=0.01)
        assert 1.4 < ratio < 1.6
    assert p.values[f"{failed}/System Output Fault"] is True
    assert powered(sim.state, plant_design, f"~IT-{hall}")


@pytest.mark.parametrize("hall", HALLS)
def test_two_ups_carry_the_halls_design_load_at_no_more_than_75_percent(plant_design, hall):
    """The top of the operating band, and beyond it up to the hall's design IT Load."""
    sim = _sim(plant_design)
    sim.schedule(_inject(START, f"~IT-{hall}", "it.load_surge", severity=1.0))
    _fail_first_ups(sim, hall)
    a = sim.state.assets
    it = a[f"~IT-{hall}"]
    assert it["utilisation"] >= plant_design.it_basis[hall].operating_max
    for ups in _hall_ups(hall)[1:]:
        assert a[ups]["mode"] == "online"
        assert a[ups]["output_kw"] == pytest.approx(it["power_kw"] / 2)
        assert a[ups]["load_pct"] <= 75.0, ups


def test_a_branch_breaker_trip_redistributes_at_once(plant_design, projector):
    sim = _sim(plant_design)
    sim.schedule(_inject(START + 10, "BCPM/5L3", "bcpm.breaker_trip"))
    sim.advance(12)
    a = sim.state.assets
    it = a["~IT-DH05"]["power_kw"]
    assert a["BCPM/5L3"]["p_kw"] == 0.0 and not a["BCPM/5L3"]["live"]
    assert a["BCPM/5L1"]["p_kw"] == pytest.approx(it / 2)
    assert a[_ups(15)]["output_kw"] == 0.0 and a[_ups(15)]["mode"] == "online"


# ---- Breakers, meters and comms


def test_meter_readings_describe_one_instant_on_the_step_a_breaker_opens(plant_design, projector):
    sim = _sim(plant_design)
    mcc = "Meter/Level 2_MSB A_8"
    sim.schedule(_inject(START + 10, mcc, "gpqm144.breaker_trip"))
    sim.advance(8)
    for _ in range(5):
        sim.step()
        p = projector.project(sim.state)
        assert_phases_balanced(p, sim.state, plant_design)
        for m in (mcc, "Meter/Meter10", "Meter/Meter11"):
            if p.values[f"{m}/V1"] == 0:
                assert p.values[f"{m}/P1"] == 0 and p.values[f"{m}/I1"] == 0, (sim.time, m)


def test_a_dead_air_supplier_delivers_no_cooling(plant_design):
    sim = _sim(plant_design)
    sim.schedule(_inject(START + 10, "Meter/Meter11", "gpm96.breaker_trip"))
    sim.advance(15)
    a = sim.state.assets
    suppliers = [s for s, _ in air_suppliers(plant_design, "DH01")]
    assert set(suppliers) == {"CRAC/L1_CRAC1", "PAHU/L1_PAHU1", "FCU/L1_FCU1"}
    for supplier in suppliers:
        assert not powered(sim.state, plant_design, supplier), supplier
        assert supplier_air(sim.state, plant_design, supplier)[0] == 0.0, supplier
    assert a["DH01"]["cooling_kw"] == 0.0  # every one of its suppliers is on Meter11
    assert a["DH05"]["cooling_kw"] > 0.0  # on Meter10


def test_a_feeder_trip_de_energises_everything_below_it(plant_design, projector):
    sim = _sim(plant_design)
    mcc = "Meter/Level 2_MSB A_8"  # airside MCC-A: roof and Level 1 air units, CDUs
    sim.schedule(_inject(START + 10, mcc, "gpqm144.breaker_trip"))
    sim.advance(15)
    a = sim.state.assets
    below = plant_design.downstream(mcc, "power", transitive=True)
    for node in (mcc, *below):
        if node in network(plant_design).meters:
            assert not a[node]["live"] and a[node]["p_kw"] == 0.0, node
        else:
            assert not powered(sim.state, plant_design, node), node
    assert a["CRAC/L1_CRAC3"]["running"] is False  # the unit lost its supply
    assert a["CRAC/G_CRAC1"]["running"] is True  # on Meter12, MSB B
    p = projector.project(sim.state)
    assert p.values["Meter/Meter10/HasAlarm"] is True
    assert p.values["Meter/Meter10/V1"] == 0.0 and p.values["Meter/Meter10/Hz"] == 0.0


def test_meter_comm_loss_greys_its_points_but_not_the_world(plant_design, projector):
    base, sim = _sim(plant_design), _sim(plant_design)
    sim.schedule(_inject(START, "Meter/Level 2_MSB A_2", "gpm96.comm_loss"))
    for s in (base, sim):
        s.advance(10)
    p = projector.project(sim.state)
    assert p.quality("Meter/Level 2_MSB A_2/Ptot") is Quality.BAD
    assert p.quality("Meter/Level 2_MSB A_3/Ptot") is Quality.GOOD
    for node, variables in base.state.assets.items():
        for name, value in variables.items():
            if name != "comm":
                assert sim.state.assets[node][name] == value, (node, name)


def test_utility_loss_applies_only_to_the_incomers(plant_design):
    spec = STANDARD_CATALOG.get("utility.incomer_loss")
    assert spec.targets(plant_design) == tuple(INC.values())
    ok = _inject(START, INC[3], "utility.incomer_loss")
    wrong = _inject(START, "Meter/Level 2_MSB A_7", "utility.incomer_loss")  # also a GPQM144
    assert STANDARD_CATALOG.check(ok, plant_design) is None
    assert "incomer" in STANDARD_CATALOG.check(wrong, plant_design)


# ---- Fuel


def test_running_gensets_burn_diesel_and_the_pump_refills_their_day_tanks(plant_design, projector):
    sim = _sim(plant_design)
    for i in (1, 2):
        sim.schedule(_inject(START, INC[i], "utility.incomer_loss"))
    tank = "Diesel/Tank 1"  # feeds gensets 1 and 2
    bulk = sim.state.assets[tank]["fuel_l"]
    pumped = False
    for _ in range(2400):
        sim.step()
        a = sim.state.assets
        pumped |= a[tank]["pump_on"]
        if a[tank]["pump_on"]:
            p = projector.project(sim.state)
            assert p.values[f"{tank}/Flowmeter - Flowmeter A"] > 0
            assert p.values[f"{tank}/Run_Stop - Fuel Pump A"] == 1
    a = sim.state.assets
    assert pumped
    assert a[tank]["fuel_l"] < bulk
    assert a[tank]["fuel_pumped_l"] == pytest.approx(bulk - a[tank]["fuel_l"])
    assert a[GEN[1]]["day_l"] > 700  # kept topped up
    assert a["Diesel/Tank 3"]["fuel_l"] == bulk  # its gensets (5, 6) never ran


def test_a_failed_fuel_pump_lets_the_day_tanks_run_down(plant_design, projector):
    sim = _sim(plant_design)
    tank = "Diesel/Tank 1"
    sim.schedule(_inject(START, tank, "diesel.fuel_pump_failure"))
    for i in (1, 2):
        sim.schedule(_inject(START, INC[i], "utility.incomer_loss"))
    sim.advance(2400)
    a = sim.state.assets
    assert not a[tank]["pump_on"] and a[tank]["fuel_pumped_l"] == 0.0
    assert a[GEN[1]]["day_l"] < 700 and a[GEN[3]]["day_l"] > 700  # genset 3 is on tank 2
    p = projector.project(sim.state)
    assert p.values[f"{tank}/System Failure_Trip - Fuel Pump A"] is True
    assert p.values[f"{tank}/HasAlarm"] is True
    assert p.values["Diesel/Tank 2/HasAlarm"] is False


# ---- Heat and KPIs


def test_ups_losses_become_heat_in_the_ups_rooms(plant_design):
    sim = _sim(plant_design)
    sim.advance(10)
    a = sim.state.assets
    for room in ("G-UPSA", "G-UPSB"):
        ups = [x.path for x in plant_design.assets_in(room) if x.type_id == "UPS"]
        assert len(ups) == 12
        assert a[room]["ups_heat_kw"] == pytest.approx(sum(a[u]["loss_kw"] for u in ups))
        assert 30 < a[room]["ups_heat_kw"] < 150
    surged = sim.fork()
    surged.schedule(_inject(surged.time, "~IT-DH02", "it.load_surge"))
    surged.advance(5)
    room = plant_design.asset(_ups(4)).room
    assert surged.state.assets[room]["ups_heat_kw"] > a[room]["ups_heat_kw"]


KPIS = (
    "Dashboard/PUE",
    "Dashboard/IT Power Chain Efficiency",
    "Dashboard/Transformer Efficiency",
    "Other/Power Losses",
    "Other/IT Load",
)


def _kpis(projector, state) -> dict[str, float | None]:
    """The power KPIs, None where the point is Bad."""
    p = projector.project(state, only=KPIS)
    return {k: None if p.quality(k) is Quality.BAD else p.values[k] for k in KPIS}


def test_the_kpis_stay_physical_on_battery_and_while_recharging(plant_design, projector):
    sim = _sim(plant_design)
    for i in INC:
        sim.schedule(_inject(START + 30, INC[i], "utility.incomer_loss"))
    on_battery = recharging = False
    for _ in range(1200):
        sim.step()
        a = sim.state.assets
        site = a[SITE]
        k = _kpis(projector, sim.state)
        ups_losses = sum(a[_ups(n)]["loss_kw"] for n in range(1, 25))  # UPS 25 is "other"
        assert k["Other/Power Losses"] == pytest.approx(site["losses_kw"], abs=0.5)
        assert site["losses_kw"] >= ups_losses - 1e-9 > 0.0
        assert k["Dashboard/PUE"] >= 1.0
        assert 90.0 < k["Dashboard/IT Power Chain Efficiency"] < 100.0
        if all(a[_ups(n)]["fed_mode"] == "battery" for n in range(1, 25)):
            on_battery = True
            assert site["storage_kw"] < 0.0  # the batteries are the source
            assert k["Dashboard/Transformer Efficiency"] is None  # no utility to measure
        if site["storage_kw"] > 0.0:
            recharging = True
    assert on_battery and recharging


def test_a_whole_site_blackout_projects_through_battery_exhaustion(plant_design, projector):
    sim = _sim(plant_design)
    for bus in (BUS_A, BUS_B):
        sim.schedule(_inject(START, bus, "ats.fail_to_transfer"))
    for i in INC:
        sim.schedule(_inject(START + 30, INC[i], "utility.incomer_loss"))
    dark = 0
    while dark < 30:
        sim.step()
        assert sim.time < START + 7200
        a = sim.state.assets
        if all(a[_ups(n)]["mode"] == "off" for n in range(1, 26)):
            dark += 1
        k = _kpis(projector, sim.state)
        assert k["Other/Power Losses"] >= 0.0
        assert k["Dashboard/PUE"] is None or k["Dashboard/PUE"] >= 1.0
        chain = k["Dashboard/IT Power Chain Efficiency"]
        assert chain is None or 0.0 < chain <= 100.0
        if sim.time > START + 31:
            assert k["Dashboard/Transformer Efficiency"] is None
        if sim.time % 60 == 0:
            projector.project(sim.state)  # every surface projects the whole world
    k = _kpis(projector, sim.state)
    assert k["Other/IT Load"] == 0.0 and k["Other/Power Losses"] == 0.0
    assert k["Dashboard/PUE"] is None and k["Dashboard/IT Power Chain Efficiency"] is None
    projector.project(sim.state)


def test_the_electrical_points_have_a_causal_source(projector, asset_model, plant_design):
    """Every point is driven by the world except the declared Compatibility Fallback debt,
    which the coverage report counts."""
    net = network(plant_design)
    physical = {*net.meters, *net.incomers, *net.ups, *net.gensets, *net.tanks, *net.branch_meters}
    physical |= {a.path for a in plant_design.assets.values() if a.type_id in ("IPS", "RCMS")}
    types = {plant_design.asset(n).type_id for n in physical}
    assert set(UNMODELLED) <= types
    for node in physical:
        unmodelled = UNMODELLED.get(plant_design.asset(node).type_id, ())
        for point in asset_model.points_of(node):
            if point.source_class is SourceClass.STATIC_METADATA:
                continue
            entry = projector.coverage.entries[point.path]
            if point.name in unmodelled:
                assert entry.source is PointSource.FALLBACK and entry.debt, point.path
            else:
                assert entry.source is not PointSource.FALLBACK, point.path
