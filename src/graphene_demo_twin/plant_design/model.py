"""The Plant Design (ADR-0002): rooms, placed assets and the typed connections between them."""

from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType


class ConnectionKind(StrEnum):
    """The physical medium a connection carries; propagation follows one kind at a time."""

    POWER = "power"
    CHW = "chw"
    """Chilled water."""
    CW = "cw"
    """Condenser water."""
    AIR = "air"
    """Supply air from an airside unit into the room it serves."""
    WATER = "water"
    """Municipal, domestic and makeup water."""
    NET = "net"
    """Control network."""
    FUEL = "fuel"


@dataclass(frozen=True, slots=True)
class Room:
    id: str
    floor: str
    name: str
    x: float
    y: float
    """Plan position of the room's corner, in metres."""
    w: float
    h: float
    kind: str
    """Use of the room: `hall`, `electrical`, `cooling`, `airside`, `water`, `core`, …"""
    fire_zone: str | None
    """Fire zone on its floor (`Zone 1`, `Support Area`); None where no zone covers it."""
    outdoor: bool


@dataclass(frozen=True, slots=True)
class Floor:
    name: str
    index: int
    """Position in the floor stack, from 0 at Ground upwards."""
    rooms: tuple[Room, ...]


@dataclass(frozen=True, slots=True)
class Shaft:
    """A vertical service shaft (riser): the one place connections of its kinds change floor."""

    id: str
    name: str
    x: float
    y: float
    """Plan position of the shaft's centre, in metres."""
    floors: tuple[str, ...]
    """Consecutive floors the shaft runs through, bottom to top."""
    carries: tuple[ConnectionKind, ...]
    """Connection kinds routed up this shaft; each kind has at most one shaft."""


@dataclass(frozen=True, slots=True)
class PlacedAsset:
    path: str
    """Export path of an Asset, or `~<name>` for an Unexported Asset."""
    type_id: str
    room: str | None
    """Room id; None only for Support Assets, which are projected but not in the world."""
    x: float | None
    y: float | None
    """Plan position within the building, in metres."""
    role: str
    system: str
    """Discipline the asset belongs to (`Cooling`, `Electrical`, `Support`, …)."""
    unexported: bool
    support: bool


@dataclass(frozen=True, slots=True)
class UnexportedAsset:
    id: str
    """Plant Design path (`~CH-004`)."""
    name: str
    type_id: str
    observed_by: tuple[str, ...]
    """Plant View folders whose points observe this asset."""


@dataclass(frozen=True, slots=True)
class ITBasis:
    """The IT Load a Data Hall is designed for, from the Plant Design basis."""

    hall: str
    design_kw: float
    operating_min: float
    operating_max: float
    """The band the hall normally runs in, as fractions of `design_kw`."""
    liquid_fraction: float
    """Share of the IT Load cooled by liquid (CDUs) rather than by the hall air."""


@dataclass(frozen=True, slots=True)
class Connection:
    """A directed physical connection: `source` supplies `target` (upstream to downstream)."""

    kind: ConnectionKind
    source: str
    target: str
    """Asset path or room id."""
    label: str = ""


class PlantDesign:
    """Read-only Plant Design. Graph nodes are placed asset paths and room ids."""

    def __init__(
        self,
        version: str,
        floors: Iterable[Floor],
        rooms: Iterable[Room],
        assets: Iterable[PlacedAsset],
        unexported: Iterable[UnexportedAsset],
        connections: Iterable[Connection],
        it_basis: Iterable[ITBasis] = (),
        shafts: Iterable[Shaft] = (),
    ) -> None:
        self._version = version
        self._floors: tuple[Floor, ...] = tuple(floors)
        self._rooms: Mapping[str, Room] = MappingProxyType({r.id: r for r in rooms})
        self._assets: Mapping[str, PlacedAsset] = MappingProxyType({a.path: a for a in assets})
        self._unexported: Mapping[str, UnexportedAsset] = MappingProxyType(
            {u.id: u for u in unexported}
        )
        self._connections: tuple[Connection, ...] = tuple(connections)
        self._it_basis: Mapping[str, ITBasis] = MappingProxyType({b.hall: b for b in it_basis})
        self._shafts: Mapping[str, Shaft] = MappingProxyType({s.id: s for s in shafts})
        self._shaft_for = {k: s for s in self.shafts.values() for k in s.carries}

        self._floors_by_name = {f.name: f for f in self.floors}
        by_room: dict[str, list[PlacedAsset]] = {rid: [] for rid in self.rooms}
        for asset in self.assets.values():
            if asset.room is not None:
                by_room[asset.room].append(asset)
        self._assets_by_room = {rid: tuple(assets) for rid, assets in by_room.items()}

        nodes = {*self.assets, *self.rooms}
        nodes.update(n for c in self.connections for n in (c.source, c.target))
        self._down: dict[str, list[Connection]] = {n: [] for n in nodes}
        self._up: dict[str, list[Connection]] = {n: [] for n in nodes}
        for c in self.connections:
            self._down[c.source].append(c)
            self._up[c.target].append(c)

    @property
    def version(self) -> str:
        return self._version

    @property
    def floors(self) -> tuple[Floor, ...]:
        return self._floors

    @property
    def rooms(self) -> Mapping[str, Room]:
        return self._rooms

    @property
    def assets(self) -> Mapping[str, PlacedAsset]:
        return self._assets

    @property
    def unexported(self) -> Mapping[str, UnexportedAsset]:
        return self._unexported

    @property
    def connections(self) -> tuple[Connection, ...]:
        return self._connections

    @property
    def it_basis(self) -> Mapping[str, ITBasis]:
        """IT Load design basis by Data Hall."""
        return self._it_basis

    @property
    def shafts(self) -> Mapping[str, Shaft]:
        return self._shafts

    def shaft_for(self, kind: ConnectionKind | str) -> Shaft | None:
        """The shaft connections of `kind` change floor in, or None if none carries it."""
        return self._shaft_for.get(ConnectionKind(kind))

    def floor(self, name: str) -> Floor:
        return self._floors_by_name[name]

    def room(self, room_id: str) -> Room:
        return self.rooms[room_id]

    def asset(self, path: str) -> PlacedAsset:
        return self.assets[path]

    def is_room(self, node: str) -> bool:
        return node in self.rooms

    def assets_in(self, room_id: str) -> tuple[PlacedAsset, ...]:
        return self._assets_by_room[room_id]

    def room_of(self, asset_path: str) -> Room | None:
        room = self.assets[asset_path].room
        return None if room is None else self.rooms[room]

    def floor_of(self, asset_path: str) -> Floor | None:
        room = self.room_of(asset_path)
        return None if room is None else self._floors_by_name[room.floor]

    def upstream(
        self, node: str, kind: ConnectionKind | str | None = None, *, transitive: bool = False
    ) -> tuple[str, ...]:
        """Nodes that supply `node`, over connections of `kind` (every kind if None).

        Direct suppliers in authored order, or with `transitive` every node reachable against
        the flow, breadth-first and nearest first. `node` itself is never included.
        """
        return self._walk(node, kind, transitive, self._up, lambda c: c.source)

    def downstream(
        self, node: str, kind: ConnectionKind | str | None = None, *, transitive: bool = False
    ) -> tuple[str, ...]:
        """Nodes that `node` supplies; the mirror of `upstream`."""
        return self._walk(node, kind, transitive, self._down, lambda c: c.target)

    @staticmethod
    def _walk(node, kind, transitive, adjacency, other) -> tuple[str, ...]:
        kind = None if kind is None else ConnectionKind(kind)
        adjacency[node]  # unknown nodes raise KeyError
        seen = {node}
        found: list[str] = []
        queue = deque([node])
        while queue:
            for c in adjacency[queue.popleft()]:
                n = other(c)
                if (kind is None or c.kind is kind) and n not in seen:
                    seen.add(n)
                    found.append(n)
                    if transitive:
                        queue.append(n)
        return tuple(found)
