"""Load `plant-design/plant-design.json` and validate it against the Asset Model."""

import json
import math
from pathlib import Path
from typing import Any

from graphene_demo_twin.asset_model import AssetModel
from graphene_demo_twin.plant_design.model import (
    Connection,
    ConnectionKind,
    Floor,
    PlacedAsset,
    PlantDesign,
    Room,
    UnexportedAsset,
)

PLANT_DESIGN_PATH = Path(__file__).resolve().parents[3] / "plant-design" / "plant-design.json"

PLANT_VIEWS: tuple[str, ...] = ("Chiller System Control", "Chiller_System", "Dashboard")
"""Export folders that observe physical assets modelled elsewhere (ADR-0004)."""


class PlantDesignError(ValueError):
    """The Plant Design disagrees with itself or with the Asset Model."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__(
            f"{len(problems)} Plant Design problem(s):\n" + "\n".join(f"- {p}" for p in problems)
        )


def load_plant_design(asset_model: AssetModel, path: Path = PLANT_DESIGN_PATH) -> PlantDesign:
    return parse_plant_design(json.loads(path.read_text(encoding="utf-8")), asset_model)


def parse_plant_design(raw: dict[str, Any], asset_model: AssetModel) -> PlantDesign:
    """Build the Plant Design from its JSON form, raising PlantDesignError on any problem."""
    problems: list[str] = []

    floor_names: list[str] = raw["floors"]
    rooms: dict[str, Room] = {}
    for r in raw["rooms"]:
        if r["id"] in rooms:
            problems.append(f"duplicate room: {r['id']}")
        if r["floor"] not in floor_names:
            problems.append(f"room {r['id']} is on an unknown floor: {r['floor']}")
        rooms[r["id"]] = Room(
            id=r["id"],
            floor=r["floor"],
            name=r["name"],
            x=r["x"],
            y=r["y"],
            w=r["w"],
            h=r["h"],
            kind=r["kind"],
            fire_zone=r["fire"],
            outdoor=r["outdoor"],
        )
    floors = [
        Floor(name, i, tuple(r for r in rooms.values() if r.floor == name))
        for i, name in enumerate(floor_names)
    ]

    unexported: dict[str, UnexportedAsset] = {}
    for u in raw["unexported"]:
        if u["id"] in unexported or u["id"] in asset_model.assets:
            problems.append(f"duplicate unexported asset: {u['id']}")
        unexported[u["id"]] = UnexportedAsset(
            id=u["id"], name=u["name"], type_id=u["type"], observed_by=tuple(u["observedBy"])
        )
        problems += _check_observed_by(unexported[u["id"]], asset_model)

    assets: dict[str, PlacedAsset] = {}
    for a in raw["assets"]:
        path = a["path"]
        if path in assets:
            problems.append(f"asset placed twice: {path}")
        if a["unexported"]:
            declared = unexported.get(path)
            if declared is None:
                problems.append(f"placed unexported asset is not declared: {path}")
            elif declared.type_id != a["type"]:
                problems.append(f"unexported asset {path} is placed as {a['type']}")
            support = False
        elif path not in asset_model.assets:
            problems.append(f"placed asset is not in the Asset Model: {path}")
            continue
        else:
            registered = asset_model.asset(path)
            support = registered.support
            if registered.type_id != a["type"]:
                problems.append(
                    f"asset {path} is placed as {a['type']}, "
                    f"but the Asset Model says {registered.type_id}"
                )
        if a["room"] is not None and a["room"] not in rooms:
            problems.append(f"asset {path} is in an unknown room: {a['room']}")
        if support and a["room"] is not None:
            problems.append(f"Support Asset is placed in a room: {path}")
        if not support:
            for coord in ("x", "y"):
                if not _is_finite_number(a[coord]):
                    problems.append(f"asset {path} has no finite {coord} position: {a[coord]!r}")
        assets[path] = PlacedAsset(
            path=path,
            type_id=a["type"],
            room=a["room"],
            x=a["x"],
            y=a["y"],
            role=a["role"],
            system=a["sys"],
            unexported=a["unexported"],
            support=support,
        )

    for path, registered in asset_model.assets.items():
        placed = assets.get(path)
        if not registered.support and (placed is None or placed.room is None):
            problems.append(f"unplaced exported asset: {path}")
    for uid in unexported:
        placed = assets.get(uid)
        if placed is None or placed.room is None:
            problems.append(f"unplaced unexported asset: {uid}")

    for rid in rooms.keys() & assets.keys():
        problems.append(f"room id is also an asset path: {rid}")

    connections: list[Connection] = []
    for e in raw["edges"]:
        where = f"{e['kind']} connection {e['a']} → {e['b']}"
        try:
            kind = ConnectionKind(e["kind"])
        except ValueError:
            problems.append(f"{where}: unknown connection kind {e['kind']}")
            continue
        for end in (e["a"], e["b"]):
            if end in rooms:
                continue
            if end not in assets:
                problems.append(f"{where}: unknown asset or room {end}")
            elif assets[end].room is None:
                problems.append(f"{where}: {end} is not in the world")
        connections.append(Connection(kind, e["a"], e["b"], e["label"]))

    if problems:
        raise PlantDesignError(problems)
    return PlantDesign(
        version=raw["version"],
        floors=floors,
        rooms=rooms.values(),
        assets=assets.values(),
        unexported=unexported.values(),
        connections=connections,
    )


def _is_finite_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


def _check_observed_by(asset: UnexportedAsset, asset_model: AssetModel) -> list[str]:
    if not asset.observed_by:
        return [f"unexported asset {asset.id} names no Plant View paths it is observed through"]
    problems = []
    for folder in asset.observed_by:
        if not any(folder == v or folder.startswith(f"{v}/") for v in PLANT_VIEWS):
            problems.append(
                f"unexported asset {asset.id} is observed outside a Plant View: {folder}"
            )
        elif not any(p.startswith(f"{folder}/") for p in asset_model.points):
            problems.append(
                f"unexported asset {asset.id} is observed through {folder}, which has no points"
            )
    return problems
