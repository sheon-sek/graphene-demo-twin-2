"""P5 Golden Demo and Event Log export/import (#28).

The Golden Demo's story is checked against the world as it plays: each step's cause must
show up in the physics and on the points, and nothing that should stay healthy fails."""

import pytest
from fastapi.testclient import TestClient

from graphene_demo_twin.event_log import (
    FORMAT,
    EventLogError,
    LogEntry,
    golden_demo,
    parse_log,
)
from graphene_demo_twin.sim import Simulation
from graphene_demo_twin.sim.electrical import RETRANSFER_S
from graphene_demo_twin.surfaces.api import create_app
from graphene_demo_twin.twin import Twin
from graphene_demo_twin.world import default_domains, default_projector

START = 1_790_000_000
BUS_A, BUS_B = "Meter/Level 2_MSB A_1", "Meter/Level 2_MSB B_9"
UPS1 = "UPS/UPS 1"
SIDE_A_UPS = [3, 4, 7]
GENS_A = [f"Genset/Genset {g}" for g in (1, 2, 3)]
CH1, CELL = "Chiller/R_C1", "Cooling Towers Plant/R_P1_CT1"


class FakeClock:
    def __init__(self, now: float = START + 0.25) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def _played(plant_design, doc) -> Simulation:
    sim = Simulation(plant_design, default_domains(), 7, START)
    for entry in doc.entries:
        sim.schedule(entry.event(START))
    return sim


@pytest.fixture(scope="module")
def projector(asset_model, plant_design):
    return default_projector(asset_model, plant_design)


def test_the_golden_demo_is_an_event_log_document():
    doc = golden_demo()
    assert doc.title and doc.description
    assert len(doc.entries) >= 8 and all(e.note for e in doc.entries)
    kinds = {e.params["fault"] for e in doc.entries}
    assert {"utility.incomer_loss", "ups.rectifier_failure", "tower.fan_failure"} <= kinds
    assert "weather.high_wet_bulb" in kinds
    assert parse_log(doc.to_json()) == doc


def test_the_golden_demo_tells_its_story_through_the_physics(plant_design, projector):
    doc = golden_demo()
    sim = _played(plant_design, doc)
    # A twin run without the tower fan failure: the same story otherwise.
    calm = _played(
        plant_design,
        type(doc)(tuple(e for e in doc.entries if e.target != CELL)),
    )

    wb0 = sim.state.assets["~WX-01"]["wet_bulb_c"]
    sim.run_until(START + 50)
    assert sim.state.assets[BUS_A]["source"] == "utility"

    # Utility loss on side A: the gensets start and the ATS transfers within 15 s, the
    # side-A UPS bridging on battery; side B never notices.
    sim.run_until(START + 60 + 20)
    a = sim.state.assets
    assert a[BUS_A]["source"] == "genset" and a[BUS_A]["live"]
    assert all(a[g]["online"] for g in GENS_A)
    assert a[BUS_B]["source"] == "utility"
    assert all(a[f"UPS/UPS {n}"]["soc"] < 1.0 for n in SIDE_A_UPS)

    # On genset power, UPS 1's rectifier fails: that one UPS goes on battery and drains.
    sim.run_until(START + 400)
    a = sim.state.assets
    assert a[UPS1]["mode"] == "battery"
    assert all(a[f"UPS/UPS {n}"]["mode"] == "online" for n in SIDE_A_UPS)
    soc = a[UPS1]["soc"]
    p = projector.project(sim.state)
    assert p.values[f"{UPS1}/Rectifier Failure"] and p.values["UPS/HasAlarm"]
    assert p.values["Genset/Genset 1/Engine Speed"] > 1400.0
    assert a["~WX-01"]["wet_bulb_c"] > wb0 + 1.0  # the humid spell builds over ~10 min

    # A tower fan fails under the high wet bulb: CH-001's condenser water runs warmer than
    # in the same story without that failure.
    sim.run_until(START + 880)
    calm.run_until(START + 880)
    a, b = sim.state.assets, calm.state.assets
    assert a[CELL]["trip"] and not b[CELL]["trip"]
    assert a[CH1]["running"] and b[CH1]["running"]
    assert a[CH1]["cw_in_c"] > b[CH1]["cw_in_c"] + 0.2
    assert a[CH1]["cond_kpa"] > b[CH1]["cond_kpa"]
    assert a[UPS1]["soc"] < soc
    p = projector.project(sim.state)
    assert p.values[f"{CELL}/System Failure_Trip"]
    assert p.values["Chiller System Control/Cooling Towers/CT-001/Alarm Status"] == "FAN TRIP"

    # The grid returns: the ATS waits for a stable supply, then retransfers.
    sim.run_until(START + 900 + RETRANSFER_S + 30)
    assert sim.state.assets[BUS_A]["source"] == "utility"

    # Every fault clears, and the world recovers by itself.
    sim.run_until(START + 1500)
    a = sim.state.assets
    assert not sim.state.faults
    assert a[UPS1]["mode"] == "online" and not a[CELL]["trip"]


def test_event_log_documents_reject_what_they_cannot_hold():
    good = golden_demo().to_json()
    for broken, message in (
        ({**good, "format": "x"}, "format"),
        ({**good, "version": 2}, "version"),
        ({**good, "events": [{"offset": -1, "kind": "k", "target": "t"}]}, "offset"),
        ({**good, "events": list(reversed(good["events"]))}, "time order"),
        ({**good, "events": [{"offset": 0, "kind": "k", "target": "t", "x": 1}]}, "unknown"),
        (
            {**good, "events": [{"offset": 0, "kind": "k", "target": "t", "params": {"a": []}}]},
            "scalar",
        ),
    ):
        with pytest.raises(EventLogError, match=message):
            parse_log(broken)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def twin(asset_model, plant_design, clock) -> Twin:
    return Twin(asset_model, plant_design, seed=7, clock=clock)


@pytest.fixture
def client(twin, tmp_path) -> TestClient:
    return TestClient(create_app(twin, console_dir=tmp_path / "missing"))


def test_the_console_plays_the_golden_demo_against_the_live_world(client, twin, clock):
    demo = client.get("/api/golden-demo").json()
    assert demo["format"] == FORMAT and demo["durationS"] == 1200

    assert client.post("/api/golden-demo/play", json={}).status_code == 422  # unconfirmed
    clock.now += 10
    r = client.post("/api/golden-demo/play", json={"confirm": True})
    assert r.status_code == 201, r.text
    body = r.json()
    start = body["start"]
    assert start == twin.frame.time and twin.frame.epoch == 1  # played from a Reset
    assert [e["at"] - start for e in body["events"]] == [e["offset"] for e in demo["events"]]
    assert len(client.get("/api/events").json()["events"]) == len(demo["events"])

    clock.now += 70  # past the grid failure
    twin.tick()
    faults = {f["fault"] for f in client.get("/api/faults").json()["faults"]}
    assert faults == {"weather.high_wet_bulb", "utility.incomer_loss"}


def test_an_exported_event_log_imports_and_replays_its_story(client, twin, clock):
    client.post("/api/golden-demo/play", json={"confirm": True})
    exported = client.get("/api/events/export").json()
    assert exported["seed"] == 7 and exported["start"] == twin.live.start_time
    demo = golden_demo()
    assert [e["offset"] for e in exported["events"]] == [e.offset for e in demo.entries]

    clock.now += 30
    r = client.post("/api/events/import", json={"log": exported, "confirm": True})
    assert r.status_code == 201, r.text
    replay = client.get("/api/events/export").json()
    assert replay["events"] == exported["events"] and replay["start"] > exported["start"]


def test_a_story_that_does_not_hold_together_is_rejected_whole(client, twin):
    doc = golden_demo()
    twice = type(doc)((*doc.entries[:2], doc.entries[1]))
    r = client.post("/api/events/import", json={"log": twice.to_json(), "reset": False})
    assert r.status_code == 422 and "already active" in r.text
    wrong = type(doc)((LogEntry(5, "fault.inject", "UPS/UPS 1", {"fault": "tower.fan_failure"}),))
    r = client.post("/api/events/import", json={"log": wrong.to_json(), "reset": False})
    assert r.status_code == 422 and "Cooling Tower" in r.text
    assert twin.frame.events == ()  # nothing logged
