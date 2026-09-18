import json

from graphene_demo_twin.config import ROOT
from graphene_demo_twin.domain.model import DomainModel, parse_utc
from graphene_demo_twin.runtime.engine import RuntimeEngine


def load_model():
    manifest = json.loads((ROOT / "config/generated/graphene-coverage-manifest.json").read_text())
    topology = json.loads((ROOT / "config/generated/graphene-instance-topology.json").read_text())
    return manifest, DomainModel(manifest, topology)


def test_meter_pqs_pf_consistency():
    manifest, model = load_model()
    snapshot = model.calculate("2026-08-28T06:00:00Z")
    instance = next(
        point["instancePath"]
        for point in manifest["points"]
        if point["typeId"] == "GPM96" and point["memberName"] == "P1"
    )
    points = {point["memberName"]: point for point in manifest["points"] if point["instancePath"] == instance}
    for phase in ("1", "2", "3"):
        if all(prefix + phase in points for prefix in ("P", "Q", "S", "PF")):
            p = snapshot.signals[points["P" + phase]["signalKey"]]
            q = snapshot.signals[points["Q" + phase]["signalKey"]]
            apparent = snapshot.signals[points["S" + phase]["signalKey"]]
            pf = snapshot.signals[points["PF" + phase]["signalKey"]]
            assert abs(apparent * apparent - (p * p + q * q)) / max(1, apparent * apparent) < 0.002
            assert abs(p / apparent - pf) < 0.002


def test_chiller_output_power_cop_invariant():
    manifest, model = load_model()
    snapshot = model.calculate("2026-08-28T06:00:00Z")
    instance = next(
        point["instancePath"]
        for point in manifest["points"]
        if point["typeId"] == "Chiller" and point["memberName"] == "COP"
    )
    points = {
        point["memberName"]: point
        for point in manifest["points"]
        if point["instancePath"] == instance
    }
    power = snapshot.signals[points["Input Power"]["signalKey"]]
    cop = snapshot.signals[points["COP"]["signalKey"]]
    output = snapshot.signals[points["Cooling Output"]["signalKey"]]
    assert abs(output - power * cop) < 0.2


def test_cooling_network_balance_closes_and_drives_facility_power():
    _, model = load_model()
    snapshot = model.calculate("2026-08-28T06:00:00Z")
    assert snapshot.world is not None
    balance = snapshot.world.balance
    assert abs(balance.cooling_demand_kw - balance.cooling_delivered_kw - balance.unmet_cooling_kw) < 0.01
    assert abs(snapshot.site["plantLoadKw"] - balance.cooling_plant_power_kw) < 0.01
    expected_facility = snapshot.site["itLoadKw"] + balance.cooling_plant_power_kw + balance.non_cooling_aux_kw
    assert abs(snapshot.site["facilityLoadKw"] - expected_facility) < 0.01
    assert abs(snapshot.site["pue"] - snapshot.site["facilityLoadKw"] / snapshot.site["itLoadKw"]) < 0.002


def test_chiller_pump_hydraulics_limit_linked_chiller_through_topology():
    _, model = load_model()
    timestamp = "2026-08-28T06:00:00Z"
    baseline = model.calculate(timestamp)
    assert baseline.world is not None

    chiller_state = next(
        state
        for state in baseline.world.assets.values()
        if state.type_id == "Chiller" and state.running
    )
    chiller_asset = next(
        asset
        for asset in model.topology["assets"]
        if asset["exportPath"] == chiller_state.export_path
    )
    assets_by_id = {asset["assetId"]: asset for asset in model.topology["assets"]}
    linked_pumps = [
        assets_by_id[relation["from"]]
        for relation in model.topology["relations"]
        if relation.get("kind") == "serves"
        and relation.get("to") == chiller_asset["assetId"]
        and relation.get("from") in assets_by_id
        and assets_by_id[relation["from"]].get("typeId") == "Chiller Pump"
    ]
    assert linked_pumps

    constraints = {
        pump["exportPath"]: {"availability": 0.0}
        for pump in linked_pumps
    }
    constrained = model.calculate(timestamp, constraints=constraints)
    assert constrained.world is not None
    constrained_chiller = constrained.world.asset(chiller_state.export_path)
    assert constrained_chiller is not None
    assert constrained_chiller.metrics["Cooling Output"] < chiller_state.metrics["Cooling Output"]
    assert constrained_chiller.flow_lps < chiller_state.flow_lps
    assert constrained.site["unmetCoolingKw"] > baseline.site["unmetCoolingKw"]
    for pump in linked_pumps:
        pump_state = constrained.world.asset(pump["exportPath"])
        assert pump_state is not None
        assert not pump_state.running
        assert pump_state.power_kw == 0.0


def test_condenser_rejection_reconciles_with_running_tower_flow():
    _, model = load_model()
    snapshot = model.calculate("2026-08-28T06:00:00Z")
    assert snapshot.world is not None

    running_towers = [
        state
        for state in snapshot.world.assets.values()
        if state.type_id == "Cooling Tower" and state.running
    ]
    assert running_towers

    summed_rejection = 0.0
    for tower in running_towers:
        rejection = float(tower.metrics["Heat Rejection"])
        cws = float(tower.metrics["CWS Temperature"])
        cwr = float(tower.metrics["CWR Temperature"])
        thermal_rejection = tower.flow_lps * 4.186 * (cwr - cws)
        assert abs(thermal_rejection - rejection) < 0.01
        summed_rejection += rejection
    assert abs(summed_rejection - snapshot.world.balance.condenser_rejection_kw) < 0.01


def test_physical_world_is_deterministic_random_access():
    _, model = load_model()
    first = model.calculate("2026-08-28T06:00:00Z")
    _ = model.calculate("2026-08-29T03:15:00Z")
    again = model.calculate("2026-08-28T06:00:00Z")
    assert first.site == again.site
    assert first.signals == again.signals
    assert first.world == again.world



def _control_seed(it_load_kw: float):
    return {
        "plantLoadKw": 0.0,
        "itLoadKw": it_load_kw,
        "facilityLoadKw": it_load_kw,
        "pue": 1.0,
        "outsideTempC": 24.0,
        "hallATempC": 23.2,
        "hallBTempC": 23.2,
        "hallARhPct": 52.0,
        "hallBRhPct": 52.0,
    }


def test_chiller_control_setpoints_drive_staging_and_capacity():
    _, model = load_model()
    timestamp = parse_utc("2026-08-28T06:00:00Z")

    low = model.world_solver.solve(timestamp, _control_seed(400.0))
    high = model.world_solver.solve(timestamp, _control_seed(4000.0))

    assert low.cooling_control.minimum_chillers == 1
    assert low.cooling_control.maximum_chillers == 4
    assert low.cooling_control.available_chillers == 3
    assert low.cooling_control.required_chillers == 1
    assert high.cooling_control.required_chillers == 3
    assert high.cooling_control.running_chillers == sum(
        1 for state in high.assets.values() if state.type_id == "Chiller" and state.running
    )
    assert all(
        state.load_fraction <= high.cooling_control.chiller_load_limit_fraction + 1e-9
        for state in high.assets.values()
        if state.type_id == "Chiller"
    )


def test_chws_control_setpoint_is_shared_by_chillers_and_cracs():
    _, model = load_model()
    world = model.world_solver.solve(parse_utc("2026-08-28T06:00:00Z"), _control_seed(400.0))
    setpoint = world.cooling_control.chws_setpoint_c

    running_chillers = [
        state for state in world.assets.values() if state.type_id == "Chiller" and state.running
    ]
    running_cracs = [
        state for state in world.assets.values() if state.type_id == "CRAC" and state.running
    ]
    assert running_chillers
    assert running_cracs
    assert all(
        abs(state.metrics["CHW Supply Temperature"] - setpoint) < 1e-9
        for state in running_chillers
    )
    assert all(
        abs(state.metrics["CHW Supply Temperature"] - setpoint) < 1e-9
        for state in running_cracs
    )


def test_graphene_chiller_control_points_project_authoritative_control_state():
    manifest, model = load_model()
    snapshot = model.calculate("2026-08-28T06:00:00Z")
    assert snapshot.world is not None
    control = snapshot.world.cooling_control
    by_path = {point["exportPath"]: point for point in manifest["points"]}

    expected = {
        "Chiller System Control/Controls/Demo Cooling Demand": control.plant_load_fraction * 100.0,
        "Chiller System Control/Plant Load": control.plant_load_fraction * 100.0,
        "Chiller System Control/Required Chillers": control.required_chillers,
        "Chiller System Control/Running Chillers": control.running_chillers,
        "Chiller System Control/Minimum Chillers": control.minimum_chillers,
        "Chiller System Control/Maximum Chillers": control.maximum_chillers,
        "Chiller System Control/Controls/Minimum Chillers": control.minimum_chillers,
        "Chiller System Control/Controls/Maximum Chillers": control.maximum_chillers,
        "Chiller System Control/Cooling Blocks/CB-001/CHWS Temperature SP": control.chws_setpoint_c,
        "Chiller System Control/Cooling Blocks/CB-001/Chiller Load Limit": (
            control.chiller_load_limit_fraction * 100.0
        ),
    }
    for path, value in expected.items():
        point = by_path[path]
        projected = snapshot.signals[point["signalKey"]]
        if isinstance(value, float):
            assert abs(projected - value) < 0.001
        else:
            assert projected == value



def test_pahu_demand_aggregates_into_shared_chw_demand_through_topology():
    _, model = load_model()
    snapshot = model.calculate("2026-08-28T06:00:00Z")
    assert snapshot.world is not None
    world = snapshot.world

    pahus = [state for state in world.assets.values() if state.type_id == "PAHU"]
    assert len(pahus) == 15
    assert all(state.running for state in pahus)

    summed_demand = sum(float(state.metrics["Cooling Demand"]) for state in pahus)
    summed_flow = sum(state.flow_lps for state in pahus)
    assert abs(summed_demand - world.balance.pahu_cooling_demand_kw) < 0.01
    assert abs(world.balance.cooling_demand_kw - world.balance.pahu_cooling_demand_kw) < 0.01
    assert abs(summed_flow - world.balance.pahu_chw_flow_lps) < 0.01
    assert abs(world.balance.zone_cooling_demand_kw - world.balance.pahu_cooling_demand_kw) < 0.01

    assets_by_id = {asset["assetId"]: asset for asset in model.topology["assets"]}
    pahu_ids = {
        asset["assetId"]: asset
        for asset in model.topology["assets"]
        if asset.get("typeId") == "PAHU"
    }
    served_pahus = {
        relation["to"]
        for relation in model.topology["relations"]
        if relation.get("kind") == "serves"
        and relation.get("to") in pahu_ids
        and relation.get("from") in assets_by_id
        and assets_by_id[relation["from"]].get("typeId") == "Chiller"
    }
    assert served_pahus == set(pahu_ids)


def test_pahu_graphene_points_project_one_authoritative_asset_state():
    manifest, model = load_model()
    snapshot = model.calculate("2026-08-28T06:00:00Z")
    assert snapshot.world is not None

    state = next(state for state in snapshot.world.assets.values() if state.type_id == "PAHU")
    points = {
        point["memberName"]: point
        for point in manifest["points"]
        if point.get("instancePath") == state.export_path
    }
    for member in (
        "Supply Air Temperature",
        "Return Air Temperature",
        "Supply Air Temperature Setpoint",
        "Return Air Temperature Setpoint",
        "Static Pressure",
        "Static Pressure Setpoint",
        "Fan Speed Command",
        "Fan Speed Feedback",
        "CHW Valve Command",
        "CHW Valve Feedback",
        "CHW Flow",
        "On_Off",
        "Fan On_Off",
    ):
        point = points[member]
        expected = state.metrics[member]
        projected = snapshot.signals[point["signalKey"]]
        if isinstance(expected, float):
            assert abs(projected - expected) < 0.001
        else:
            assert projected == expected


def test_energy_is_monotonic_random_access():
    manifest, model = load_model()
    point = next(point for point in manifest["points"] if point["sourceClass"] == "ENERGY_INTEGRAL" and point["runtimeRequired"])
    a = model.calculate("2026-08-28T00:00:00Z").signals[point["signalKey"]]
    b = model.calculate("2026-08-29T00:00:00Z").signals[point["signalKey"]]
    again = model.calculate("2026-08-28T00:00:00Z").signals[point["signalKey"]]
    assert b > a >= 0 and a == again


def test_demo_holds_but_open_world_does_not():
    runtime = RuntimeEngine()
    runtime.set_mode("demo")
    runtime.seek("2026-08-29T00:00:00Z")
    runtime.state = "RUNNING"
    runtime._sim_anchor = parse_utc("2026-08-29T00:00:00Z")
    assert runtime.now() <= parse_utc(runtime.cfg["demoEndUtc"])
    assert runtime.state == "HOLDING"

    runtime.set_mode("open_world")
    runtime.seek("2026-09-30T00:00:00Z")
    runtime.state = "RUNNING"
    runtime._sim_anchor = parse_utc("2026-09-30T00:00:00Z")
    _ = runtime.now()
    assert runtime.state == "RUNNING"
