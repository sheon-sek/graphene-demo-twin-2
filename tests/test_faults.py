import json

from graphene_demo_twin.config import ROOT
from graphene_demo_twin.domain.model import DomainModel
from graphene_demo_twin.faults.engine import FaultActivation, FaultEngine
from graphene_demo_twin.runtime.engine import RuntimeEngine


def fixture():
    manifest = json.loads((ROOT / "config/generated/graphene-coverage-manifest.json").read_text())
    topology = json.loads((ROOT / "config/generated/graphene-instance-topology.json").read_text())
    model = DomainModel(manifest, topology)
    snapshot = model.calculate("2026-08-28T02:30:00Z")
    return manifest, topology, model, snapshot


def point_for(manifest, instance_path, *members):
    for point in manifest["points"]:
        if point.get("instancePath") == instance_path and point["memberName"] in members:
            return point
    raise AssertionError((instance_path, members))


def test_crac_valve_fault_correlated_symptoms_are_solved_before_projection():
    manifest, topology, model, baseline = fixture()
    valve = next(
        point
        for point in manifest["points"]
        if point["typeId"] == "CRAC" and point["memberName"] == "CHW Valve Feedback"
    )
    target = valve["instancePath"]
    fault = FaultActivation("x", "CRAC_VALVE_STUCK", target, 1.0, baseline.timestamp.isoformat())
    engine = FaultEngine()
    constraints = engine.physical_constraints([fault], topology)
    solved = model.calculate(baseline.timestamp, constraints=constraints)

    feedback = point_for(manifest, target, "CHW Valve Feedback")
    flow = point_for(manifest, target, "CHW Flow")
    sat = point_for(manifest, target, "Supply Air Temperature", "SAT")

    assert solved.signals[feedback["signalKey"]] <= 3.1
    assert solved.signals[flow["signalKey"]] < baseline.signals[flow["signalKey"]]
    assert solved.signals[sat["signalKey"]] > baseline.signals[sat["signalKey"]]
    assert solved.site["unmetCoolingKw"] > baseline.site["unmetCoolingKw"]
    assert solved.site["hallATempC"] > baseline.site["hallATempC"]


def test_cooling_tower_failure_propagates_through_topology_to_chiller_and_site_balance():
    runtime = RuntimeEngine()
    runtime.set_mode("open_world")
    runtime.seek("2026-08-28T06:00:00Z")
    baseline = runtime.snapshot()

    assets_by_id = {asset["assetId"]: asset for asset in runtime.topology["assets"]}
    tower = None
    chiller = None
    tower_power = None
    for candidate in runtime.topology["assets"]:
        if candidate["typeId"] != "Cooling Tower":
            continue
        candidate_power = point_for(
            runtime.manifest, candidate["exportPath"], "Power", "Electrical Power"
        )
        if baseline["points"][candidate_power["exportPath"]]["value"] <= 0:
            continue
        linked = next(
            (
                relation
                for relation in runtime.topology["relations"]
                if relation.get("kind") == "serves"
                and relation.get("from") == candidate["assetId"]
                and relation.get("to") in assets_by_id
                and assets_by_id[relation["to"]].get("typeId") == "Chiller"
            ),
            None,
        )
        if linked is None:
            continue
        candidate_chiller = assets_by_id[linked["to"]]
        candidate_cop = point_for(runtime.manifest, candidate_chiller["exportPath"], "COP")
        if baseline["points"][candidate_cop["exportPath"]]["value"] <= 0:
            continue
        tower = candidate
        chiller = candidate_chiller
        tower_power = candidate_power
        break

    assert tower is not None
    assert chiller is not None
    assert tower_power is not None

    tower_speed = point_for(runtime.manifest, tower["exportPath"], "Fan Speed Feedback")
    chiller_cop = point_for(runtime.manifest, chiller["exportPath"], "COP")
    chiller_power = point_for(runtime.manifest, chiller["exportPath"], "Input Power", "Power")
    chiller_cws = point_for(runtime.manifest, chiller["exportPath"], "CW Supply Temperature")

    runtime.faults.inject("COOLING_TOWER_FAILURE", tower["exportPath"], 1.0, runtime.now())
    faulted = runtime.snapshot()

    assert faulted["points"][tower_power["exportPath"]]["value"] == 0.0
    assert faulted["points"][tower_speed["exportPath"]]["value"] == 0.0
    assert faulted["points"][chiller_cws["exportPath"]]["value"] > baseline["points"][chiller_cws["exportPath"]]["value"]
    assert faulted["points"][chiller_cop["exportPath"]]["value"] < baseline["points"][chiller_cop["exportPath"]]["value"]
    assert faulted["points"][chiller_power["exportPath"]]["value"] > baseline["points"][chiller_power["exportPath"]]["value"]
    assert faulted["site"]["plantLoadKw"] > baseline["site"]["plantLoadKw"]
    assert faulted["site"]["facilityLoadKw"] > baseline["site"]["facilityLoadKw"]
    assert faulted["site"]["pue"] > baseline["site"]["pue"]


def test_network_failure_changes_quality_and_state():
    manifest, _, _, snapshot = fixture()
    point = next(
        point
        for point in manifest["points"]
        if point["typeId"] == "Network Device" and point["memberName"] == "Ping Time"
    )
    target = point["instancePath"]
    effective = FaultEngine().apply(
        snapshot,
        manifest,
        [FaultActivation("x", "NETWORK_DEVICE_FAILURE", target, 1.0, snapshot.timestamp.isoformat())],
    )
    assert effective["values"][point["signalKey"]] == 9999.0
    assert effective["quality"][point["signalKey"]] == "Bad_CommunicationError"
