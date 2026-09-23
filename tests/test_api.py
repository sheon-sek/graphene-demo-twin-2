from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from graphene_demo_twin.surfaces.api import create_app
from graphene_demo_twin.twin import Twin

START = 1_790_000_000
HOT_AISLE_DH03 = "Temperature and Humidity/Datahall 3/Sensor 17/Temp"
COLON_PATH = "Genset/Genset 2/AC Voltage: L2-N"
COOLING_LOSS = {"fault": "placeholder.cooling_loss", "severity": 0.8}


class FakeClock:
    def __init__(self, now: float = START + 0.25) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def twin(asset_model, plant_design, clock) -> Twin:
    return Twin(asset_model, plant_design, seed=7, clock=clock)


@pytest.fixture
def client(twin, tmp_path) -> TestClient:
    return TestClient(create_app(twin, console_dir=tmp_path / "missing"))


def _url(path: str) -> str:
    return quote(path, safe="/")


def test_status_reports_the_published_frame(client, twin, clock):
    clock.now += 3
    twin.tick()
    body = client.get("/api/status").json()
    assert body["time"] == START + 3
    assert body["timestamp"] == "2026-09-21T14:13:23Z"
    assert body["seq"] == 3 and body["epoch"] == 0  # one frame per second stepped
    assert body["seed"] == 7
    assert body["events"] == 0
    assert body["coverage"]["total"] == 8741


def test_assets_list_every_asset_with_its_placement(client, asset_model):
    assets = client.get("/api/assets").json()
    assert [a["path"] for a in assets] == list(asset_model.assets)
    crac = next(a for a in assets if a["path"] == "CRAC/L1_CRAC3")
    assert crac["typeId"] == "CRAC"
    assert crac["room"] == "DH03" and crac["floor"] == "Level 1"
    assert crac["pointCount"] == len(asset_model.points_of("CRAC/L1_CRAC3"))
    support = next(a for a in assets if a["support"])
    assert support["room"] is None


def test_asset_detail_has_readings_and_direct_connections(client, asset_model):
    body = client.get(f"/api/assets/{_url('CRAC/L1_CRAC3')}").json()
    assert [p["path"] for p in body["points"]] == [
        p.path for p in asset_model.points_of("CRAC/L1_CRAC3")
    ]
    assert body["downstream"] == {"air": ["DH03"]}
    meter = client.get(f"/api/assets/{_url('BCPM/3L1')}").json()
    assert meter["upstream"]["power"] and meter["downstream"]["power"] == ["DH03"]
    assert client.get("/api/assets/CRAC/L1_CRAC9").status_code == 404


def test_points_carry_value_quality_and_sim_timestamp(client, twin, clock):
    clock.now += 60
    twin.tick()
    body = client.get("/api/points", params={"path": [HOT_AISLE_DH03, COLON_PATH]}).json()
    assert body["time"] == START + 60
    by_path = {p["path"]: p for p in body["points"]}
    hot = by_path[HOT_AISLE_DH03]
    assert hot["value"] == twin.frame.projection.values[HOT_AISLE_DH03]
    assert hot["quality"] == "good"
    assert hot["timestamp"] == "2026-09-21T14:14:20Z"
    assert hot["source"] == "physics"
    assert by_path[COLON_PATH]["source"] == "fallback"


def test_points_filter_by_prefix_and_asset(client, asset_model):
    prefix = client.get("/api/points", params={"prefix": "Dashboard/Energy"}).json()["points"]
    assert prefix and all(p["path"].startswith("Dashboard/Energy") for p in prefix)
    asset = client.get("/api/points", params={"asset": "BCPM/3L1"}).json()["points"]
    assert [p["path"] for p in asset] == [p.path for p in asset_model.points_of("BCPM/3L1")]
    everything = client.get("/api/points").json()["points"]
    assert len(everything) == 8741


def test_point_detail_by_url_encoded_path(client):
    body = client.get(f"/api/points/{_url(COLON_PATH)}").json()
    assert body["path"] == COLON_PATH
    assert body["dataType"] == "Float4"
    assert body["asset"] == "Genset/Genset 2"
    assert body["debt"] is True
    assert client.get("/api/points/No/Such/Point").status_code == 404


def test_state_exposes_asset_state_per_node(client, twin):
    body = client.get("/api/state").json()
    assert body["time"] == START
    assert body["assets"]["DH03"] == twin.frame.state.assets["DH03"]
    assert client.get("/api/state/DH03").json()["state"] == twin.frame.state.assets["DH03"]
    assert client.get("/api/state/NOPE").status_code == 404


def test_plant_design_graph(client, plant_design):
    body = client.get("/api/plant-design").json()
    assert body["version"] == plant_design.version
    assert [f["name"] for f in body["floors"]] == [f.name for f in plant_design.floors]
    assert len(body["rooms"]) == len(plant_design.rooms)
    assert len(body["assets"]) == len(plant_design.assets)
    assert len(body["connections"]) == len(plant_design.connections)
    assert {"kind", "source", "target", "label"} <= body["connections"][0].keys()
    assert {u["id"] for u in body["unexported"]} == set(plant_design.unexported)
    hydraulic = next(s for s in body["shafts"] if "chw" in s["carries"])
    shaft = plant_design.shafts[hydraulic["id"]]
    assert hydraulic == {
        "id": shaft.id,
        "name": shaft.name,
        "x": shaft.x,
        "y": shaft.y,
        "floors": list(shaft.floors),
        "carries": [k.value for k in shaft.carries],
    }


def test_operator_actions_append_to_the_event_log(client, twin):
    created = client.post(
        "/api/events", json={"kind": "fault.inject", "target": "DH03", "params": COOLING_LOSS}
    )
    assert created.status_code == 201
    assert created.json()["at"] == START
    log = client.get("/api/events").json()
    assert [(e["kind"], e["target"], e["params"]) for e in log["events"]] == [
        ("fault.inject", "DH03", COOLING_LOSS)
    ]
    assert len(twin.live.events) == 1

    rejected = client.post(
        "/api/events", json={"kind": "fault.inject", "target": "DH99", "params": COOLING_LOSS}
    )
    assert rejected.status_code == 422
    assert "DH99" in rejected.json()["detail"]


def test_the_event_log_is_read_from_the_published_frame(client, twin, clock):
    clock.now += 3  # the clock moves on before the operator acts
    client.post(
        "/api/events", json={"kind": "fault.inject", "target": "DH03", "params": COOLING_LOSS}
    )
    status = client.get("/api/status").json()
    log = client.get("/api/events").json()
    points = client.get("/api/points", params={"path": HOT_AISLE_DH03}).json()
    assert log["time"] == status["time"] == points["time"] == START + 3
    assert status["events"] == len(log["events"]) == 1

    # A change the twin has not published yet is not shown.
    twin.live.submit("fault.clear", "DH03", {"fault": "placeholder.cooling_loss"})
    assert len(client.get("/api/events").json()["events"]) == 1


def test_reset_needs_confirmation_and_clears_the_log(client, twin):
    event = {"kind": "fault.inject", "target": "DH03", "params": COOLING_LOSS}
    assert client.post("/api/events", json=event).status_code == 201
    assert client.post("/api/reset", json={}).status_code == 422
    assert client.post("/api/reset", json={"confirm": False}).status_code == 422
    assert len(twin.live.events) == 1

    body = client.post("/api/reset", json={"confirm": True}).json()
    assert body["epoch"] == 1
    assert client.get("/api/events").json()["events"] == []


def test_forks_create_advance_and_read(client, twin, clock):
    clock.now += 10
    twin.tick()
    created = client.post("/api/forks")
    assert created.status_code == 201
    fork = created.json()
    assert fork["forkedAt"] == fork["time"] == START + 10

    event = client.post(
        f"/api/forks/{fork['id']}/events",
        json={"kind": "fault.inject", "target": "DH03", "params": COOLING_LOSS},
    )
    assert event.status_code == 201
    advanced = client.post(f"/api/forks/{fork['id']}/advance", json={"seconds": 3600}).json()
    assert advanced["time"] == START + 10 + 3600
    assert len(advanced["events"]) == 1

    preview = client.get(f"/api/forks/{fork['id']}/points", params={"path": HOT_AISLE_DH03})
    hot = preview.json()["points"][0]
    assert hot["timestamp"] == "2026-09-21T15:13:30Z"
    assert hot["value"] > twin.frame.projection.values[HOT_AISLE_DH03] + 5
    state = client.get(f"/api/forks/{fork['id']}/state").json()
    assert state["time"] == START + 3610

    # The Live World saw none of it.
    assert twin.live.events == ()
    assert client.get("/api/forks").json()[0]["id"] == fork["id"]
    assert client.get(f"/api/forks/{fork['id']}").json()["time"] == START + 3610
    assert client.delete(f"/api/forks/{fork['id']}").status_code == 204
    assert client.get(f"/api/forks/{fork['id']}").status_code == 404


def test_fork_requests_are_validated(client):
    fork = client.post("/api/forks").json()
    advance = f"/api/forks/{fork['id']}/advance"
    assert client.post(advance, json={"seconds": 0}).status_code == 422
    assert client.post(advance, json={"seconds": 7 * 86_400}).status_code == 422
    bad_event = client.post(
        f"/api/forks/{fork['id']}/events",
        json={"kind": "fault.inject", "target": "DH03", "params": COOLING_LOSS, "at": 1},
    )
    assert bad_event.status_code == 422
    assert client.post("/api/forks/fork-999/advance", json={"seconds": 1}).status_code == 404


def test_coverage_counts_and_report(client, twin):
    body = client.get("/api/coverage").json()
    assert body["total"] == 8741
    assert body["counts"] == twin.projector.coverage.counts()
    assert sum(body["counts"].values()) == body["total"]
    assert body["debt"] == twin.projector.coverage.debt()

    report = client.get("/api/coverage/points").json()
    assert len(report) == 8741
    assert {"path", "source", "sourceClass", "alarmBit", "support", "debt"} <= report[0].keys()
    alarm_bit = {e["path"]: e["alarmBit"] for e in report}
    assert alarm_bit["DemoRack/Breaker1/Trip"] is True
    assert alarm_bit["Chiller System Control/Alarms/Active Count"] is False
    fallback = client.get("/api/coverage/points", params={"source": "fallback"}).json()
    assert len(fallback) == body["counts"]["fallback"]
    debt = client.get("/api/coverage/points", params={"debt": True}).json()
    assert len(debt) == body["debt"]


def test_console_is_served_when_built(twin, tmp_path):
    (tmp_path / "index.html").write_text("<!doctype html><title>Console</title>")
    client = TestClient(create_app(twin, console_dir=tmp_path))
    assert "<title>Console</title>" in client.get("/").text
    assert client.get("/api/status").status_code == 200


def test_a_placeholder_page_stands_in_for_an_unbuilt_console(client):
    page = client.get("/")
    assert page.status_code == 200
    assert "not built" in page.text
