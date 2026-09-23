import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from graphene_demo_twin.plant_design import (
    PLANT_DESIGN_PATH,
    PLANT_VIEWS,
    Connection,
    ConnectionKind,
    PlantDesign,
    PlantDesignError,
    parse_plant_design,
)

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def raw_design() -> dict:
    return json.loads(PLANT_DESIGN_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def raw(raw_design) -> dict:
    return copy.deepcopy(raw_design)


def _errors(raw: dict, asset_model) -> str:
    with pytest.raises(PlantDesignError) as excinfo:
        parse_plant_design(raw, asset_model)
    return str(excinfo.value)


# ---- Loading


def test_regenerating_the_design_matches_the_committed_json(tmp_path):
    out = tmp_path / "plant-design.json"
    subprocess.run(
        [sys.executable, "plant-design/author.py", str(out)],
        cwd=REPO,
        check=True,
        capture_output=True,
    )
    assert out.read_bytes() == PLANT_DESIGN_PATH.read_bytes()


def test_loads_floors_rooms_assets_and_connections(plant_design):
    assert [f.name for f in plant_design.floors] == ["Ground", "Level 1", "Level 2", "Roof"]
    assert len(plant_design.rooms) == 33
    assert len(plant_design.assets) == 639 + 18
    assert len(plant_design.unexported) == 18
    assert len(plant_design.connections) == 406


def test_room_carries_floor_plan_rectangle_and_fire_zone(plant_design):
    hall = plant_design.room("DH05")
    assert hall.floor == "Level 2"
    assert hall.kind == "hall"
    assert hall.fire_zone == "Zone 1"
    assert (hall.x, hall.y, hall.w, hall.h) == (0, 0, 24, 30)
    assert not hall.outdoor
    assert plant_design.room("G-GEN").outdoor
    assert plant_design.room("G-CORE").fire_zone is None


def test_floor_lists_its_rooms(plant_design):
    level1 = plant_design.floor("Level 1")
    assert level1.index == 1
    assert {"DH01", "DH02", "DH03", "DH04", "L1-SUP"} <= {r.id for r in level1.rooms}
    assert all(r.floor == "Level 1" for r in level1.rooms)


def test_placed_asset_is_resolved_against_the_asset_model(plant_design, asset_model):
    chiller = plant_design.asset("Chiller/R_C1")
    assert chiller.type_id == "Chiller" == asset_model.asset("Chiller/R_C1").type_id
    assert chiller.room == "R-CHP"
    assert (chiller.x, chiller.y) == (4, 24)
    assert chiller.system == "Cooling"
    assert not chiller.unexported
    assert not chiller.support
    exported = {a.path for a in plant_design.assets.values() if not a.unexported}
    assert exported == set(asset_model.assets)


def test_support_assets_are_unplaced(plant_design, asset_model):
    support = {p for p, a in asset_model.assets.items() if a.support}
    unplaced = {a.path for a in plant_design.assets.values() if a.room is None}
    assert unplaced == support
    assert plant_design.asset("Dashboard/1A").support


def test_unexported_assets_are_the_ones_the_prd_fixes(plant_design):
    assert set(plant_design.unexported) == {
        "~CH-004",
        *(f"~CB-00{i}" for i in range(1, 9)),
        *(f"~CCU-00{i}" for i in range(1, 9)),
        "~P-CWS-01",
    }


def test_plant_design_is_read_only(plant_design):
    for name in ("version", "floors", "rooms", "assets", "unexported", "connections"):
        with pytest.raises(AttributeError):
            setattr(plant_design, name, ())


def test_unexported_asset_is_placed_and_observed_through_plant_views(plant_design):
    ch4 = plant_design.unexported["~CH-004"]
    assert ch4.name == "CH-004"
    assert ch4.type_id == "Chiller"
    assert ch4.observed_by == (
        "Chiller System Control/Chillers/CH-004",
        "Chiller_System/Chillers/CH-004",
    )
    placed = plant_design.asset("~CH-004")
    assert placed.unexported
    assert placed.room == "R-CHP"


def test_connections_are_typed(plant_design):
    kinds = {c.kind for c in plant_design.connections}
    assert kinds == set(ConnectionKind)
    first = plant_design.connections[0]
    assert first.kind is ConnectionKind.CHW
    assert (first.source, first.target, first.label) == (
        "Chiller/R_CP1",
        "Chiller/R_C1",
        "primary CHW",
    )


def test_plant_views_are_the_three_observation_folders():
    assert PLANT_VIEWS == ("Chiller System Control", "Chiller_System", "Dashboard")


# ---- Graph queries


def test_downstream_by_connection_kind(plant_design):
    assert plant_design.downstream("Chiller/R_C1", "cw") == tuple(
        f"Cooling Towers Plant/R_P1_CT{c}" for c in range(1, 6)
    )
    assert plant_design.downstream("Chiller/R_C1", ConnectionKind.CHW) == ("Chiller/R_CV9",)
    assert plant_design.downstream("Chiller/R_C1", ConnectionKind.POWER) == ()


def test_upstream_by_connection_kind(plant_design):
    assert plant_design.upstream("Chiller/R_C1", ConnectionKind.POWER) == ("Meter/Level 2_MSB A_4",)
    assert set(plant_design.upstream("Chiller/R_C1", ConnectionKind.CHW)) == {
        "Chiller/R_CP1",
        "Chiller/R_CV1",
    }
    assert plant_design.upstream("Genset/Genset 1", ConnectionKind.FUEL) == ("Diesel/Tank 1",)


def test_without_a_kind_every_connection_counts(plant_design):
    assert set(plant_design.upstream("Chiller/R_C1")) == {
        "Chiller/R_CP1",
        "Chiller/R_CV1",
        "Chiller/R_CP5",
        "Chiller/R_CV5",
        "Meter/Level 2_MSB A_4",
    }


def test_transitive_upstream_follows_the_whole_chain(plant_design):
    chain = plant_design.upstream("BCPM/1L1", ConnectionKind.POWER, transitive=True)
    assert chain[:3] == ("UPS/UPS 1", "Meter/Level 2_MSB A_2", "Meter/Level 2_MSB A_1")
    assert {"Meter/SPPA Incomer 1", "Genset/Genset 1"} <= set(chain)
    assert "BCPM/1L1" not in chain
    assert len(chain) == len(set(chain))


def test_transitive_traversal_terminates_on_loops():
    loop = PlantDesign(
        version="test",
        floors=(),
        rooms=(),
        assets=(),
        unexported=(),
        connections=(
            Connection(ConnectionKind.CHW, "pump", "chiller"),
            Connection(ConnectionKind.CHW, "chiller", "hall"),
            Connection(ConnectionKind.CHW, "hall", "pump"),
        ),
    )
    assert loop.downstream("pump", ConnectionKind.CHW, transitive=True) == ("chiller", "hall")
    assert loop.upstream("pump", ConnectionKind.CHW, transitive=True) == ("hall", "chiller")


def test_transitive_downstream_reaches_the_halls(plant_design):
    down = plant_design.downstream("Chiller/R_CP9", ConnectionKind.CHW, transitive=True)
    assert {f"~CCU-00{i}" for i in range(1, 9)} <= set(down)
    assert "TIW/CDU-01" in down


def test_rooms_are_graph_nodes(plant_design):
    assert set(plant_design.upstream("DH01", ConnectionKind.POWER)) == {
        "BCPM/1L1",
        "BCPM/1L2",
        "BCPM/1L3",
    }
    assert "CRAC/L1_CRAC1" in plant_design.upstream("DH01", ConnectionKind.AIR)
    assert plant_design.is_room("DH01")
    assert not plant_design.is_room("CRAC/L1_CRAC1")


def test_unknown_graph_node_raises_key_error(plant_design):
    with pytest.raises(KeyError):
        plant_design.downstream("Chiller/R_C9", ConnectionKind.POWER)


def test_assets_in_a_room(plant_design):
    in_dh01 = {a.path for a in plant_design.assets_in("DH01")}
    assert {"~CB-001", "~CCU-001", "BCPM/1L1", "CRAC/L1_CRAC1"} <= in_dh01
    assert all(plant_design.room_of(p).id == "DH01" for p in in_dh01)
    assert plant_design.assets_in("G-CORE") == ()
    with pytest.raises(KeyError):
        plant_design.assets_in("DH99")


def test_room_and_floor_of_an_asset(plant_design):
    assert plant_design.room_of("UPS/UPS 1").id == "G-UPSA"
    assert plant_design.floor_of("UPS/UPS 1").name == "Ground"
    assert plant_design.floor_of("~CB-005").name == "Level 2"
    assert plant_design.room_of("Dashboard/1A") is None
    assert plant_design.floor_of("Dashboard/1A") is None
    with pytest.raises(KeyError):
        plant_design.room_of("Chiller/R_C9")


# ---- Validation


def test_committed_design_is_valid(plant_design):
    assert plant_design.version == "0.1"


def test_fails_when_an_exported_asset_is_unplaced(raw, asset_model):
    raw["assets"] = [a for a in raw["assets"] if a["path"] != "Chiller/R_C1"]
    assert "unplaced exported asset: Chiller/R_C1" in _errors(raw, asset_model)


def test_fails_when_an_exported_asset_has_no_room(raw, asset_model):
    ups = next(a for a in raw["assets"] if a["path"] == "UPS/UPS 1")
    ups["room"] = None
    assert "unplaced exported asset: UPS/UPS 1" in _errors(raw, asset_model)


def test_support_assets_may_be_left_out(raw, asset_model):
    raw["assets"] = [a for a in raw["assets"] if not a["path"].startswith("Smart Alarm Logic/")]
    design = parse_plant_design(raw, asset_model)
    assert "Smart Alarm Logic/Breaker/Breaker0" not in design.assets


def test_fails_when_a_placed_path_is_not_in_the_asset_model(raw, asset_model):
    raw["assets"].append({**raw["assets"][0], "path": "Chiller/R_C9"})
    assert "Chiller/R_C9" in _errors(raw, asset_model)


def test_fails_when_a_placed_type_disagrees_with_the_asset_model(raw, asset_model):
    chiller = next(a for a in raw["assets"] if a["path"] == "Chiller/R_C1")
    chiller["type"] = "Pump"
    assert "Chiller/R_C1" in _errors(raw, asset_model)


@pytest.mark.parametrize("path", ["Chiller/R_C1", "~CH-004"])
@pytest.mark.parametrize("coord", ["x", "y"])
@pytest.mark.parametrize("value", [None, "4", True, float("nan"), float("inf")])
def test_fails_when_a_placed_asset_lacks_a_finite_position(raw, asset_model, path, coord, value):
    placed = next(a for a in raw["assets"] if a["path"] == path)
    placed[coord] = value
    assert f"asset {path} has no finite {coord} position" in _errors(raw, asset_model)


def test_fails_when_a_placed_asset_is_in_an_unknown_room(raw, asset_model):
    chiller = next(a for a in raw["assets"] if a["path"] == "Chiller/R_C1")
    chiller["room"] = "R-NOPE"
    assert "R-NOPE" in _errors(raw, asset_model)


def test_fails_when_a_room_is_on_an_unknown_floor(raw, asset_model):
    raw["rooms"][0]["floor"] = "Basement"
    assert "Basement" in _errors(raw, asset_model)


@pytest.mark.parametrize("end", ["a", "b"])
def test_fails_when_a_connection_references_an_unknown_asset(raw, asset_model, end):
    raw["edges"].append({"kind": "power", "a": "UPS/UPS 1", "b": "BCPM/1L1", "label": ""})
    raw["edges"][-1][end] = "UPS/UPS 99"
    assert "UPS/UPS 99" in _errors(raw, asset_model)


def test_fails_when_a_connection_references_an_unknown_room(raw, asset_model):
    raw["edges"].append({"kind": "air", "a": "CRAC/L1_CRAC1", "b": "DH99", "label": ""})
    assert "DH99" in _errors(raw, asset_model)


def test_fails_when_a_connection_reaches_an_unplaced_support_asset(raw, asset_model):
    raw["edges"].append({"kind": "net", "a": "Dashboard/1A", "b": "UPS/UPS 1", "label": ""})
    assert "Dashboard/1A" in _errors(raw, asset_model)


def test_fails_on_an_unknown_connection_kind(raw, asset_model):
    raw["edges"].append({"kind": "steam", "a": "UPS/UPS 1", "b": "BCPM/1L1", "label": ""})
    assert "steam" in _errors(raw, asset_model)


def test_fails_when_an_unexported_asset_lacks_plant_view_paths(raw, asset_model):
    ch4 = next(u for u in raw["unexported"] if u["id"] == "~CH-004")
    ch4["observedBy"] = []
    assert "~CH-004" in _errors(raw, asset_model)


def test_fails_when_an_unexported_asset_is_observed_outside_a_plant_view(raw, asset_model):
    ch4 = next(u for u in raw["unexported"] if u["id"] == "~CH-004")
    ch4["observedBy"] = ["Chiller/R_C1"]
    assert "Chiller/R_C1" in _errors(raw, asset_model)


def test_fails_when_a_plant_view_path_has_no_points(raw, asset_model):
    ch4 = next(u for u in raw["unexported"] if u["id"] == "~CH-004")
    ch4["observedBy"] = ["Chiller_System/Chillers/CH-005"]
    assert "Chiller_System/Chillers/CH-005" in _errors(raw, asset_model)


def test_fails_when_an_unexported_asset_is_not_placed(raw, asset_model):
    raw["assets"] = [a for a in raw["assets"] if a["path"] != "~CH-004"]
    assert "unplaced unexported asset: ~CH-004" in _errors(raw, asset_model)


def test_fails_when_a_placed_unexported_asset_is_not_declared(raw, asset_model):
    raw["unexported"] = [u for u in raw["unexported"] if u["id"] != "~CH-004"]
    assert "~CH-004" in _errors(raw, asset_model)


def test_fails_on_duplicate_placement(raw, asset_model):
    raw["assets"].append(next(a for a in raw["assets"] if a["path"] == "Chiller/R_C1"))
    assert "Chiller/R_C1" in _errors(raw, asset_model)


def test_reports_every_problem_at_once(raw, asset_model):
    raw["assets"] = [a for a in raw["assets"] if a["path"] != "Chiller/R_C1"]
    raw["edges"].append({"kind": "air", "a": "CRAC/L1_CRAC1", "b": "DH99", "label": ""})
    message = _errors(raw, asset_model)
    assert "Chiller/R_C1" in message
    assert "DH99" in message
