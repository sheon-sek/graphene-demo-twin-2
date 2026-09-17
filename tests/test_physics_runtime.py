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


def test_physical_world_is_deterministic_random_access():
    _, model = load_model()
    first = model.calculate("2026-08-28T06:00:00Z")
    _ = model.calculate("2026-08-29T03:15:00Z")
    again = model.calculate("2026-08-28T06:00:00Z")
    assert first.site == again.site
    assert first.signals == again.signals
    assert first.world == again.world


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
