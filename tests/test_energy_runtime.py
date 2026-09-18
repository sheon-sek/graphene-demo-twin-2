from datetime import timedelta

from graphene_demo_twin.domain.model import parse_utc
from graphene_demo_twin.runtime.engine import RuntimeEngine


def _running_tower_points(runtime: RuntimeEngine, snapshot: dict):
    for energy in runtime.manifest["points"]:
        if (
            energy["typeId"] != "Cooling Tower"
            or energy["memberName"] != "Energy"
            or energy["instancePath"].lower() not in runtime.model.authoritative_energy_assets
        ):
            continue
        power = next(
            point
            for point in runtime.manifest["points"]
            if point["instancePath"] == energy["instancePath"]
            and point["memberName"] == "Power"
        )
        if snapshot["points"][power["exportPath"]]["value"] > 0:
            return energy, power
    raise AssertionError("expected a running Cooling Tower with Power/Energy points")


def test_cooling_tower_energy_integrates_authoritative_power():
    runtime = RuntimeEngine()
    runtime.set_mode("open_world")
    start = parse_utc("2026-08-28T06:00:00Z")
    runtime.seek(start)
    first = runtime.snapshot()
    energy, power = _running_tower_points(runtime, first)

    runtime.seek(start + timedelta(seconds=runtime.model.energy_step_seconds))
    second = runtime.snapshot()

    delta_kwh = (
        second["points"][energy["exportPath"]]["value"]
        - first["points"][energy["exportPath"]]["value"]
    )
    power_kw = first["points"][power["exportPath"]]["value"]
    assert abs(delta_kwh - power_kw * runtime.model.energy_step_seconds / 3600.0) < 0.002


def test_injected_tower_fault_changes_energy_only_after_activation_and_recovers_slope():
    runtime = RuntimeEngine()
    runtime.set_mode("open_world")
    start = parse_utc("2026-08-28T06:00:00Z")
    runtime.seek(start)
    baseline = runtime.snapshot()
    energy, power = _running_tower_points(runtime, baseline)
    initial_energy = baseline["points"][energy["exportPath"]]["value"]

    injected = runtime.faults.inject(
        "COOLING_TOWER_FAILURE",
        energy["instancePath"],
        1.0,
        runtime.now(),
    )
    fault_start = runtime.snapshot()
    assert fault_start["points"][power["exportPath"]]["value"] == 0.0
    assert abs(fault_start["points"][energy["exportPath"]]["value"] - initial_energy) < 0.001

    after_fault = start + timedelta(seconds=2 * runtime.model.energy_step_seconds)
    runtime.seek(after_fault)
    faulted = runtime.snapshot()
    assert abs(faulted["points"][energy["exportPath"]]["value"] - initial_energy) < 0.001

    comparison = RuntimeEngine()
    comparison.set_mode("open_world")
    comparison.seek(after_fault)
    unfaulted = comparison.snapshot()
    assert (
        unfaulted["points"][energy["exportPath"]]["value"]
        > faulted["points"][energy["exportPath"]]["value"]
    )

    runtime.faults.clear(injected.injectionId, runtime.now())
    cleared = runtime.snapshot()
    assert abs(
        cleared["points"][energy["exportPath"]]["value"]
        - faulted["points"][energy["exportPath"]]["value"]
    ) < 0.001

    recovered_at = after_fault + timedelta(seconds=runtime.model.energy_step_seconds)
    runtime.seek(recovered_at)
    recovered = runtime.snapshot()
    recovered_delta = (
        recovered["points"][energy["exportPath"]]["value"]
        - cleared["points"][energy["exportPath"]]["value"]
    )
    recovered_power = cleared["points"][power["exportPath"]]["value"]
    assert recovered_power > 0
    assert abs(
        recovered_delta - recovered_power * runtime.model.energy_step_seconds / 3600.0
    ) < 0.002

    before = start - timedelta(seconds=runtime.model.energy_step_seconds)
    runtime.seek(before)
    comparison.seek(before)
    assert (
        runtime.snapshot()["points"][power["exportPath"]]["value"]
        == comparison.snapshot()["points"][power["exportPath"]]["value"]
    )


def test_scripted_tower_fault_updates_authoritative_energy(monkeypatch):
    runtime = RuntimeEngine()
    runtime.set_mode("open_world")
    start = parse_utc("2026-08-28T06:00:00Z")
    runtime.seek(start)
    baseline = runtime.snapshot()
    energy, power = _running_tower_points(runtime, baseline)

    scenario = {
        "id": "SCRIPTED_TOWER_ENERGY_TEST",
        "recipeId": "COOLING_TOWER_FAILURE",
        "targetType": "Cooling Tower",
        "targetMatch": energy["instancePath"].rsplit("/", 1)[-1],
        "startLocal": start.isoformat(),
        "endLocal": (start + timedelta(minutes=30)).isoformat(),
        "rampSeconds": 0,
    }
    monkeypatch.setattr(
        "graphene_demo_twin.runtime.engine.demo_scenarios",
        lambda: {"scenarios": [scenario]},
    )
    runtime.set_mode("demo")
    runtime.seek(start)
    fault_start = runtime.snapshot()
    initial_energy = fault_start["points"][energy["exportPath"]]["value"]
    assert fault_start["points"][power["exportPath"]]["value"] == 0.0

    runtime.seek(start + timedelta(seconds=2 * runtime.model.energy_step_seconds))
    faulted = runtime.snapshot()
    assert abs(faulted["points"][energy["exportPath"]]["value"] - initial_energy) < 0.001



def test_indirect_pahu_constraint_updates_authoritative_tower_energy_slope():
    runtime = RuntimeEngine()
    runtime.set_mode("open_world")
    start = parse_utc("2026-08-28T11:30:00Z")
    runtime.seek(start)
    baseline = runtime.snapshot()
    energy, power = _running_tower_points(runtime, baseline)

    target = next(
        asset
        for asset in runtime.topology["assets"]
        if asset.get("typeId") == "PAHU" and asset["exportPath"] == "PAHU/R_PAHU2"
    )
    initial_energy = baseline["points"][energy["exportPath"]]["value"]

    runtime.faults.inject("PAHU_AFTER_HOURS", target["exportPath"], 1.0, runtime.now())
    fault_start = runtime.snapshot()
    fault_power = fault_start["points"][power["exportPath"]]["value"]
    assert fault_power > baseline["points"][power["exportPath"]]["value"]
    assert abs(fault_start["points"][energy["exportPath"]]["value"] - initial_energy) < 0.001

    runtime.seek(start + timedelta(seconds=runtime.model.energy_step_seconds))
    after = runtime.snapshot()
    delta_kwh = after["points"][energy["exportPath"]]["value"] - initial_energy
    assert abs(
        delta_kwh - fault_power * runtime.model.energy_step_seconds / 3600.0
    ) < 0.002



def test_energy_constraint_timeline_excludes_unrelated_long_ramp():
    runtime = RuntimeEngine()
    runtime.set_mode("demo")
    timestamp = parse_utc("2026-08-28T11:30:00Z")
    segments = runtime.scripted_energy_segments(timestamp)

    assert segments
    assert all(
        segment.targetAsset.lower() in runtime._energy_constraint_targets()
        for segment in segments
    )
    assert all(
        segment.recipeId != "CHILLER_CONDENSER_DEGRADATION"
        for segment in segments
    )
