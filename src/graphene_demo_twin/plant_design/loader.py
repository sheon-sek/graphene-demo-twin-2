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
    ITBasis,
    PlacedAsset,
    PlantDesign,
    Room,
    Shaft,
    UnexportedAsset,
)

PLANT_DESIGN_PATH = Path(__file__).resolve().parents[3] / "plant-design" / "plant-design.json"

PLANT_VIEWS: tuple[str, ...] = ("Chiller System Control", "Chiller_System", "Dashboard")
"""Export folders that observe physical assets modelled elsewhere (ADR-0004)."""
HALL_AGGREGATES = "Environment Monitoring"
"""Each Data Hall's folder here (`Environment Monitoring/<floor>/<hall>`) also observes: its
loose points, outside any sensor's UDT instance, aggregate the hall (cold aisle, IT Load)."""


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
        problems += _check_observed_by(unexported[u["id"]], asset_model, rooms)

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

    it_basis: dict[str, ITBasis] = {}
    for b in raw["basis"]["it"]:
        room = rooms.get(b["hall"])
        if room is None or room.kind != "hall":
            problems.append(f"IT basis names a room that is not a Data Hall: {b['hall']}")
        if b["hall"] in it_basis:
            problems.append(f"duplicate IT basis for {b['hall']}")
        if (basis := _it_basis(b)) is None:
            problems.append(f"IT basis for {b['hall']} is out of range")
        else:
            it_basis.setdefault(b["hall"], basis)
    halls = {r.id for r in rooms.values() if r.kind == "hall"}
    if missing := sorted(halls - it_basis.keys()):
        problems.append(f"Data Halls without an IT basis: {missing}")
    shafts, shaft_problems = _parse_shafts(raw["shafts"], floor_names, rooms)
    problems += shaft_problems
    problems += _check_floor_changes(connections, shafts, rooms, assets)
    problems += _check_phases(connections, asset_model)
    gateways, gateway_problems = _parse_gateways(raw.get("gateways", {}), assets)
    problems += gateway_problems

    if problems:
        raise PlantDesignError(problems)
    return PlantDesign(
        version=raw["version"],
        floors=floors,
        rooms=rooms.values(),
        assets=assets.values(),
        unexported=unexported.values(),
        connections=connections,
        it_basis=it_basis.values(),
        shafts=shafts,
        gateways=gateways,
    )


GATEWAY_FOLDER = "Network Topology"
"""Where the gateways the design names (`GATEWAY A`) sit in the Asset Model."""


def _parse_gateways(
    raw: dict[str, list[str]], assets: dict[str, PlacedAsset]
) -> tuple[dict[str, tuple[str, ...]], list[str]]:
    """Gateway asset path → the systems it carries; a system is carried by at most one."""
    problems: list[str] = []
    systems = {a.system for a in assets.values()}
    gateways: dict[str, tuple[str, ...]] = {}
    carrier: dict[str, str] = {}
    for name, carried in raw.items():
        path = f"{GATEWAY_FOLDER}/{name}"
        if path not in assets or assets[path].type_id != "Network Device":
            problems.append(f"gateway {name} is not a placed Network Device ({path})")
            continue
        for system in carried:
            if system not in systems:
                problems.append(f"gateway {name} carries an unknown system: {system}")
            elif system in carrier:
                problems.append(f"system {system} is carried by {carrier[system]} and {name}")
            carrier.setdefault(system, name)
        gateways[path] = tuple(carried)
    return gateways, problems


def _parse_shafts(
    raw_shafts: list[dict[str, Any]], floor_names: list[str], rooms: dict[str, Room]
) -> tuple[list[Shaft], list[str]]:
    problems: list[str] = []
    shafts: dict[str, Shaft] = {}
    carriers: dict[ConnectionKind, list[str]] = {}
    for s in raw_shafts:
        sid = s["id"]
        if sid in shafts:
            problems.append(f"duplicate shaft: {sid}")
        unknown = [f for f in s["floors"] if f not in floor_names]
        problems += [f"shaft {sid} is on an unknown floor: {f}" for f in unknown]
        indices = [floor_names.index(f) for f in s["floors"] if f not in unknown]
        if not indices or indices != list(range(indices[0], indices[0] + len(indices))):
            problems.append(f"shaft {sid} does not run through consecutive floors: {s['floors']}")
        carries: list[ConnectionKind] = []
        for k in s["carries"]:
            try:
                carries.append(ConnectionKind(k))
            except ValueError:
                problems.append(f"shaft {sid} carries an unknown connection kind: {k}")
                continue
            carriers.setdefault(carries[-1], []).append(sid)
        for f in s["floors"]:
            if f not in unknown and not _inside_a_room(s["x"], s["y"], f, rooms):
                problems.append(f"shaft {sid} is outside every room on {f}")
        shafts[sid] = Shaft(sid, s["name"], s["x"], s["y"], tuple(s["floors"]), tuple(carries))
    for kind, ids in carriers.items():
        if len(ids) > 1:
            problems.append(f"{kind} is carried by more than one shaft: {', '.join(ids)}")
    return list(shafts.values()), problems


def _inside_a_room(x: Any, y: Any, floor: str, rooms: dict[str, Room]) -> bool:
    if not (_is_finite_number(x) and _is_finite_number(y)):
        return False
    return any(
        r.floor == floor and r.x <= x <= r.x + r.w and r.y <= y <= r.y + r.h for r in rooms.values()
    )


def _check_floor_changes(
    connections: list[Connection],
    shafts: list[Shaft],
    rooms: dict[str, Room],
    assets: dict[str, PlacedAsset],
) -> list[str]:
    """Every connection that changes floor must have a shaft of its kind reaching both floors."""

    def floor(node: str) -> str | None:
        room = node if node in rooms else getattr(assets.get(node), "room", None)
        return rooms[room].floor if room in rooms else None

    shaft_for = {k: s for s in shafts for k in s.carries}
    problems = []
    for c in connections:
        a, b = floor(c.source), floor(c.target)
        if a is None or b is None or a == b:
            continue
        shaft = shaft_for.get(c.kind)
        if shaft is None or not {a, b} <= set(shaft.floors):
            problems.append(
                f"{c.kind} connection {c.source} → {c.target} changes floor from {a} to {b}, "
                f"but no shaft carries {c.kind} between them"
            )
    return problems


def _phases(node: str, asset_model: AssetModel) -> int | None:
    """How many phases a meter measures, from the points it has; None for anything else."""
    if node not in asset_model.assets:
        return None
    names = {p.name for p in asset_model.points_of(node)}
    if "V1" not in names:
        return None
    return 3 if "V2" in names else 1


def _check_phases(connections: list[Connection], asset_model: AssetModel) -> list[str]:
    """A single-phase meter's circuit cannot feed a three-phase meter."""
    problems = []
    for c in connections:
        if c.kind is not ConnectionKind.POWER or _phases(c.target, asset_model) != 3:
            continue
        if _phases(c.source, asset_model) == 1:
            problems.append(
                f"power connection {c.source} → {c.target} feeds a three-phase meter "
                "from a single-phase one"
            )
    return problems


def _it_basis(b: dict[str, Any]) -> ITBasis | None:
    """One hall's IT basis, or None unless its design power is finite and positive, its
    operating band a finite range within (0, 100] % and its liquid share in [0, 1)."""
    design, band, liquid = b["design_kW"], b["operating_pct"], b["liquid_fraction"]
    if not (_is_finite_number(design) and design > 0.0):
        return None
    if not (isinstance(band, list) and len(band) == 2 and all(map(_is_finite_number, band))):
        return None
    low, high = (p / 100.0 for p in band)
    if not 0.0 < low <= high <= 1.0:
        return None
    if not (_is_finite_number(liquid) and 0.0 <= liquid < 1.0):
        return None
    return ITBasis(b["hall"], float(design), low, high, float(liquid))


def _is_finite_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


def _check_observed_by(
    asset: UnexportedAsset, asset_model: AssetModel, rooms: dict[str, Room]
) -> list[str]:
    if not asset.observed_by:
        return [f"unexported asset {asset.id} names no Plant View paths it is observed through"]
    problems = []
    for folder in asset.observed_by:
        if folder.startswith(f"{HALL_AGGREGATES}/"):
            problems += _check_hall_aggregate(asset, folder, asset_model, rooms)
        elif not any(folder == v or folder.startswith(f"{v}/") for v in PLANT_VIEWS):
            problems.append(
                f"unexported asset {asset.id} is observed outside a Plant View: {folder}"
            )
        elif not any(p.startswith(f"{folder}/") for p in asset_model.points):
            problems.append(
                f"unexported asset {asset.id} is observed through {folder}, which has no points"
            )
    return problems


def _check_hall_aggregate(
    asset: UnexportedAsset, folder: str, asset_model: AssetModel, rooms: dict[str, Room]
) -> list[str]:
    parts = folder.split("/")
    room = rooms.get(parts[-1]) if len(parts) == 3 else None
    if room is None or room.kind != "hall" or room.floor != parts[1]:
        return [f"unexported asset {asset.id} is observed through {folder}, not a Data Hall"]
    if not any(
        path.startswith(f"{folder}/") and "/" not in path[len(folder) + 1 :] and p.asset is None
        for path, p in asset_model.points.items()
    ):
        return [
            f"unexported asset {asset.id} is observed through {folder}, "
            "which has no hall aggregate points"
        ]
    return []
