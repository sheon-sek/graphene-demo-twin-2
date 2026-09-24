"""P4 control network and communication quality (#25): the BMS network's devices, the port
view of its switches, the field gateways and what losing each does to point quality."""

import pytest
from conftest import load_asset_model, load_plant_design

from graphene_demo_twin.faults import FaultParams, preview_fault
from graphene_demo_twin.plant_design import PlantDesign
from graphene_demo_twin.projection import PointSource, Projector
from graphene_demo_twin.projection.values import Quality
from graphene_demo_twin.sim import Event, Simulation
from graphene_demo_twin.sim.network import network
from graphene_demo_twin.world import default_domains

START = 1_790_000_000
GW_A = "Network Topology/GATEWAY A"
GW_B = "Network Topology/GATEWAY B"
CORE_A = "Network Topology/MAIN CORE SWITCH A"
DIST_A = "Network Topology/SERVER DISTRIBUTION SWITCH A"
EWS_A = "Network Topology/EWS-A"
EWS_B = "Network Topology/EWS-B"
OWS_A1 = "Network Topology/OWS-A1"
SERVER_A = "Network Topology/DATABASE SERVER A"
SERVER_B = "Network Topology/DATABASE SERVER B"
SWITCH_VIEW = "Network Switches/MAIN CORE SWITCH A"
CRAC3 = "CRAC/L1_CRAC3"  # a cooling field device, behind GATEWAY A
METER = "Meter/SPPA Incomer 1"  # an electrical field device, behind GATEWAY B
TH = "Temperature and Humidity/Datahall 3/Sensor 17"  # environment, behind GATEWAY B


@pytest.fixture(scope="module")
def asset_model():
    return load_asset_model()


@pytest.fixture(scope="module")
def plant_design(asset_model) -> PlantDesign:
    return load_plant_design(asset_model)


@pytest.fixture(scope="module")
def projector(asset_model, plant_design) -> Projector:
    from graphene_demo_twin.world import default_projector

    return default_projector(asset_model, plant_design)


def _sim(plant_design, seed: int = 7) -> Simulation:
    return Simulation(plant_design, default_domains(), seed, START)


def _inject(at: int, target: str, fault: str, **params) -> Event:
    return Event(at, "fault.inject", target, {"fault": fault, **params})


def _clear(at: int, target: str, fault: str) -> Event:
    return Event(at, "fault.clear", target, {"fault": fault})


# ---- The network the Plant Design authors


def test_the_design_splits_the_field_between_two_gateways(plant_design):
    net = network(plant_design)
    assert net.gateways == {
        "Cooling": GW_A,
        "Airside": GW_A,
        "Water": GW_A,
        "Electrical": GW_B,
        "Environment": GW_B,
        "Life Safety": GW_B,  # fire protection and lifts (#26)
    }
    assert {CRAC3, "TIW/CDU-01"} <= set(net.field[GW_A])
    assert {METER, TH} <= set(net.field[GW_B])


# ---- Device gauges, and the two views of one switch agreeing


def test_device_gauges_sit_in_their_room_and_status_follows_them(plant_design, projector):
    sim = _sim(plant_design)
    sim.advance(600)
    p = projector.project(sim.state)
    for device in (CORE_A, GW_A, SERVER_A, OWS_A1):
        s = sim.state.assets[device]
        assert 5.0 <= s["cpu_pct"] <= 60.0, device
        assert 30.0 <= s["mem_pct"] <= 70.0, device
        assert 25.0 <= s["temp_c"] <= 55.0, device
        assert 0.0 < s["ping_ms"] < 20.0, device
        assert p.values[f"{device}/Status"] == 0, device
        assert 179 <= p.values[f"{device}/Uptime"] <= 210, device  # 180 days less the boot jitter
    room = sim.state.assets["L1-SUP"]["temp_c"]
    assert p.values[f"{CORE_A}/Temperature"] == pytest.approx(
        room + 22.0 + 0.08 * p.values[f"{CORE_A}/CPU"], abs=1.0
    )


def test_the_port_view_reads_the_same_device_as_the_topology_view(plant_design, projector):
    """v1 #2: the same physical switch never shows healthy on one view and failed on the
    other."""
    sim = _sim(plant_design)
    sim.schedule(_inject(START, DIST_A, "network.device_down", severity=0.3))
    sim.advance(5)
    p = projector.project(sim.state)
    for member in ("CPU", "Memory", "Temperature", "Ping Time", "Comm", "Status", "Uptime"):
        assert (
            p.values[f"{DIST_A}/{member}"]
            == p.values[f"Network Switches/SERVER DISTRIBUTION SWITCH A/{member}"]
        ), member
        assert p.quality(f"{DIST_A}/{member}") == p.quality(
            f"Network Switches/SERVER DISTRIBUTION SWITCH A/{member}"
        )


def test_switch_ports_show_their_links(plant_design, projector):
    sim = _sim(plant_design)
    sim.advance(15)
    p = projector.project(sim.state)
    net = network(plant_design)
    ports = net.ports[CORE_A]
    up = [pt for pt in ports if pt.peer is not None]
    assert 4 <= len(up) <= 10
    for pt in ports:
        at = f"{SWITCH_VIEW}/Ports/Port {pt.number:02d}"
        if pt.peer is None:
            assert p.values[f"{at}/Admin Status"] == 2
            assert p.values[f"{at}/Display Status"] == 0
            assert p.values[f"{at}/Speed"] == 0
            assert p.values[f"{at}/Description"] == "Spare"
        else:
            assert p.values[f"{at}/Admin Status"] == 1
            assert p.values[f"{at}/Display Status"] == 1
            assert p.values[f"{at}/Speed"] == (10_000 if pt.number >= 41 else 1_000)
            assert p.values[f"{at}/Description"] == pt.peer.rsplit("/", 1)[-1]
            assert p.values[f"{at}/Link Status"] == 1
            assert 0.0 < p.values[f"{at}/In Utilization"] <= 100.0
    # The switch's counters add up from its ports, and the two views agree on the totals.
    assert p.values[f"{SWITCH_VIEW}/Ports Up"] == len(up)
    assert p.values[f"{SWITCH_VIEW}/Port Count"] == 48
    assert p.values[f"{SWITCH_VIEW}/Ports Off"] == 48 - len(up)


def test_the_gateway_status_points_follow_the_gateways(plant_design, projector):
    sim = _sim(plant_design)
    sim.schedule(_inject(START, GW_A, "network.gateway_failure"))
    sim.advance(3)
    p = projector.project(sim.state)
    assert p.values["Other/Gateway 1 Status"] is False
    assert p.values["Other/Gateway 2 Status"] is True


# ---- Gateway failure: the acceptance criterion


def test_gateway_a_failure_turns_its_systems_bad_and_leaves_the_rest_good(plant_design, projector):
    base, sim = _sim(plant_design), _sim(plant_design)
    sim.schedule(_inject(START, GW_A, "network.gateway_failure"))
    sim.advance(5)
    p = projector.project(sim.state)
    # Every cooling, airside and water point is Bad...
    for path in (
        f"{CRAC3}/Supply Air Temperature",
        "Chiller/R_C1/Condenser Pressure",
        "Chiller/R_CP9/Power",
        "TIW/CDU-01/New Tag",
        "AC Makeup Tank/G_P1/On_Off",
        "Chiller System Control/Chillers/CH-001/Chilled Water Supply Temp",
        "Chiller_System/Main Headers/TS-01/Temperature",
    ):
        assert p.quality(path) is Quality.BAD, path
    # ...while electrical and environment stay Good, and so do the KPIs the supervisor itself
    # computes and the network devices on other paths.
    for path in (
        f"{METER}/Ptot",
        f"{TH}/Temp",
        "Environment Monitoring/Level 1/DH03/Avg Cold Aisle Temp",
        "Dashboard/1A/PUE",
        "Other/IT Load",
        "BCPM/3L1/Active Power",
    ):
        assert p.quality(path) is Quality.GOOD, path
    assert p.quality(f"{GW_B}/CPU") is Quality.GOOD
    assert p.quality(f"{CORE_A}/CPU") is Quality.GOOD
    # Bad points hold the last value that got through, not a zero (checked below on the
    # projector, where the hold is visible step to step).
    base.advance(5)
    assert sim.state.assets["DH03"]["temp_c"] == base.state.assets["DH03"]["temp_c"]
    # The gateway itself is down: its points are Bad, and it reports Disconnected.
    assert p.quality(f"{GW_A}/CPU") is Quality.BAD
    assert p.values[f"{GW_A}/Comm"] == 1 and p.values[f"{GW_A}/Status"] == 1


def test_gateway_b_failure_turns_electrical_and_environment_bad(plant_design, projector):
    sim = _sim(plant_design)
    sim.schedule(_inject(START, GW_B, "network.gateway_failure"))
    sim.advance(3)
    p = projector.project(sim.state)
    assert p.quality(f"{METER}/Ptot") is Quality.BAD
    assert p.quality(f"{TH}/Temp") is Quality.BAD
    assert p.quality(f"{CRAC3}/Supply Air Temperature") is Quality.GOOD
    assert p.quality("Chiller/R_C1/Condenser Pressure") is Quality.GOOD


# ---- Switch failure, device_down and path choice


def test_a_switch_failure_isolates_what_only_it_reaches(plant_design, projector):
    sim = _sim(plant_design)
    sim.schedule(_inject(START, DIST_A, "network.switch_failure"))
    sim.advance(3)
    p = projector.project(sim.state)
    assert p.quality(f"{EWS_A}/Status") is Quality.BAD
    assert p.quality(f"{OWS_A1}/CPU") is Quality.BAD
    # The redundancy holds: everything with a path round the dead switch is still good, and
    # the field devices' points never went through the distribution layer anyway.
    assert p.quality(f"{EWS_B}/Status") is Quality.GOOD
    assert p.quality(f"{GW_A}/CPU") is Quality.GOOD
    assert p.quality(f"{CRAC3}/Supply Air Temperature") is Quality.GOOD


def test_device_down_keeps_its_place_in_the_catalogue(plant_design, projector):
    sim = _sim(plant_design)
    sim.schedule(_inject(START, DIST_A, "network.device_down", severity=0.3))
    sim.advance(3)
    p = projector.project(sim.state)
    assert p.quality(f"{EWS_A}/Status") is Quality.UNCERTAIN


# ---- Port flap


def test_a_port_flap_flaps_its_link_and_degrades_the_path(plant_design, projector):
    sim = _sim(plant_design)
    sim.schedule(_inject(START, SWITCH_VIEW, "network.port_flap", severity=0.5))
    at = f"{SWITCH_VIEW}/Ports/Port 41"
    seen_down = seen_up = seen_uncertain = False
    for _ in range(120):
        sim.step()
        s = sim.state.assets[CORE_A]
        if s["flap_down"]:
            seen_down = True
            q = projector.project(sim.state, only=(f"{at}/Link Status", f"{at}/Display Status"))
            assert q.values[f"{at}/Link Status"] == 2
            assert q.values[f"{at}/Display Status"] == 2
        elif seen_down:
            seen_up |= (
                projector.project(sim.state, only=(f"{at}/Link Status",)).values[
                    f"{at}/Link Status"
                ]
                == 1
            )
        seen_uncertain |= (
            projector.project(sim.state, only=(f"{GW_A}/CPU",)).quality(f"{GW_A}/CPU")
            is Quality.UNCERTAIN
        )
    assert seen_down and seen_up and seen_uncertain
    assert sim.state.assets[CORE_A]["flap_errors"] > 0
    # The switch works harder and warns while the link is cycling.
    assert sim.state.assets[CORE_A]["cpu_pct"] > 30.0
    assert projector.project(sim.state).values[f"{CORE_A}/Status"] == 2
    assert projector.project(sim.state).values[f"{SWITCH_VIEW}/Status"] == 2


# ---- Server overload and NTP drift


def test_a_server_overload_speaks_slowly_but_keeps_its_pair(plant_design, projector):
    sim = _sim(plant_design)
    sim.schedule(_inject(START, SERVER_A, "network.server_overload"))
    sim.advance(10)
    p = projector.project(sim.state)
    s = sim.state.assets[SERVER_A]
    assert s["cpu_pct"] == 100.0
    assert s["mem_pct"] > 90.0
    assert p.values[f"{SERVER_A}/Ping Time"] > 100.0
    assert p.values[f"{SERVER_A}/Status"] == 2  # warning, not offline
    # The physical world, and every field device, is untouched: the redundant pair carries.
    assert p.quality(f"{CRAC3}/Supply Air Temperature") is Quality.GOOD


def test_ntp_drift_grows_until_clear_and_warns_past_a_second(plant_design, projector):
    sim = _sim(plant_design)
    ntp = "Network Topology/NTP SERVER A"
    sim.schedule(_inject(START, ntp, "network.ntp_drift", severity=1.0))
    sim.advance(120)
    assert sim.state.assets[ntp]["clock_offset_s"] == pytest.approx(2.0, abs=0.1)
    assert projector.project(sim.state).values[f"{ntp}/Status"] == 2
    sim.schedule(_clear(sim.time, ntp, "network.ntp_drift"))
    sim.step()
    assert sim.state.assets[ntp]["clock_offset_s"] == 0.0
    assert projector.project(sim.state).values[f"{ntp}/Status"] == 0


# ---- Quality is observation only, and Clear recovers


def test_network_faults_change_nothing_physical(plant_design):
    base, sim = _sim(plant_design), _sim(plant_design)
    for target, fault in (
        (GW_A, "network.gateway_failure"),
        (DIST_A, "network.switch_failure"),
        (SWITCH_VIEW, "network.port_flap"),
        (SERVER_A, "network.server_overload"),
    ):
        sim.schedule(_inject(START, target, fault))
    sim.advance(120)
    base.advance(120)
    for node, variables in base.state.assets.items():
        if node in sim.state.assets and node.startswith(("Network Topology", "Network Switches")):
            continue
        # The airside's Loss of Signal reads the device's comm, so its `comm` variable and
        # the alarm bits it raises follow the gateway without the world itself changing;
        # compare everything else.
        sim_vars = {
            k: v
            for k, v in sim.state.assets[node].items()
            if k != "comm" and not k.startswith("alarm_loss") and k != "has_alarm"
        }
        base_vars = {
            k: v
            for k, v in variables.items()
            if k != "comm" and not k.startswith("alarm_loss") and k != "has_alarm"
        }
        assert sim_vars == base_vars, node


def test_clearing_a_gateway_failure_reboots_it_and_restores_quality(
    plant_design, asset_model, projector
):
    sim = _sim(plant_design)
    sim.schedule(_inject(START, GW_A, "network.gateway_failure", ramp_min=1))
    sim.advance(600)
    p = projector.project(sim.state)
    assert p.quality(f"{CRAC3}/Supply Air Temperature") is Quality.BAD
    assert sim.state.assets[GW_A]["up"] is False
    sim.schedule(_clear(sim.time, GW_A, "network.gateway_failure"))
    sim.advance(5)
    p = projector.project(sim.state)
    assert p.quality(f"{CRAC3}/Supply Air Temperature") is Quality.GOOD
    assert sim.state.assets[GW_A]["up"] is True
    assert p.values[f"{GW_A}/Uptime"] == 0  # a reboot, not a snap back to 180 days


def test_a_dead_switch_holds_the_last_value_not_a_zero(plant_design, projector):
    sim = _sim(plant_design)
    sim.schedule(_inject(START, OWS_A1, "network.device_down", severity=1.0))
    sim.advance(5)
    p = projector.project(sim.state)
    assert p.quality(f"{OWS_A1}/CPU") is Quality.BAD
    assert p.values[f"{OWS_A1}/CPU"] > 5.0  # the last polled value, not a default


# ---- Preview


def test_a_gateway_preview_lists_the_field_and_keeps_the_other_systems(
    plant_design, asset_model, projector
):
    preview = preview_fault(
        _sim(plant_design),
        projector,
        asset_model,
        GW_A,
        "network.gateway_failure",
        FaultParams(),
        60,
    )
    nodes = [a.node for a in preview.affected]
    assert nodes[0] == GW_A
    assert GW_B not in nodes and CORE_A not in nodes
    assert not any(
        a.node.startswith(("Meter/", "Environment Monitoring/")) for a in preview.affected
    )
    assert any(a.node == CRAC3 for a in preview.affected)
    assert any(
        d.path == f"{CRAC3}/Supply Air Temperature" and d.predicted_quality == "bad"
        for d in preview.diffs
    )


def test_a_port_flap_preview_shows_the_error_count_climb(plant_design, asset_model, projector):
    preview = preview_fault(
        _sim(plant_design),
        projector,
        asset_model,
        SWITCH_VIEW,
        "network.port_flap",
        FaultParams(severity=0.5),
        60,
    )
    diff = next(d for d in preview.diffs if d.path == f"{SWITCH_VIEW}/Ports/Port 41/Error Count")
    assert diff.predicted > diff.base


# ---- A lost point holds the last value that got through


def test_a_lost_point_holds_its_last_good_value(plant_design, projector):
    sim = _sim(plant_design)
    sim.schedule(_inject(START, GW_A, "network.gateway_failure"))
    sim.advance(2)
    p2 = projector.project(sim.state)
    path = f"{CRAC3}/Return Air Temperature"
    assert p2.quality(path) is Quality.BAD
    before = p2.values[path]
    sim.advance(30)  # the unit keeps running, so its true return air moves on
    p3 = projector.project(sim.state, hold=p2)
    assert p3.values[path] == before
    assert p3.quality(path) is Quality.BAD
    # And the point is whole again once the gateway recovers.
    sim.schedule(_clear(sim.time, GW_A, "network.gateway_failure"))
    sim.advance(3)
    p4 = projector.project(sim.state)
    assert p4.quality(path) is Quality.GOOD
    assert p4.values[path] != before


# ---- Coverage: the network's points are physics now


def test_the_network_points_are_bound_and_the_port_view_is_complete(
    plant_design, asset_model, projector
):
    net = network(plant_design)
    for d in net.devices:
        for point in asset_model.points_of(d):
            assert projector.coverage.entries[point.path].source is PointSource.PHYSICS, point.path
    for view in net.switches:
        assert all(
            entry.source is PointSource.PHYSICS
            for path, entry in projector.coverage.entries.items()
            if path.startswith(f"{view}/")
        )
    assert projector.coverage.entries["Other/Gateway 1 Status"].source is PointSource.PHYSICS
