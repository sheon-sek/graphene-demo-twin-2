"""The Asset Model registry: every UDT type, UDT instance and Point, keyed by export path."""

import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from graphene_demo_twin.asset_model.classify import SourceClass, is_alarm_bit


@dataclass(frozen=True, slots=True)
class UdtType:
    type_id: str
    """Ignition typeId, including its folder (`Production/GPM96`)."""
    parent: str | None
    """typeId this type inherits from, if any."""
    members: tuple[str, ...]
    """Atomic member paths relative to an instance, including nested UDT instances' members."""


@dataclass(frozen=True, slots=True)
class UdtInstance:
    path: str
    type_id: str
    asset: str
    """Export path of the root UDT instance (the Asset) this instance belongs to."""
    support: bool

    @property
    def is_asset(self) -> bool:
        return self.path == self.asset


@dataclass(frozen=True, slots=True)
class Point:
    path: str
    """Export path; the registry key and the OPC UA NodeId suffix."""
    name: str
    """Member name: the last path element."""
    member: str | None
    """Path relative to the Asset (`Ports/Port 01/Speed`); None outside any UDT instance."""
    data_type: str
    type_id: str | None
    """typeId of the nearest enclosing UDT instance; None outside any UDT instance."""
    value_source: str
    eng_unit: str | None
    udt_instance: str | None
    """Export path of the nearest enclosing UDT instance."""
    asset: str | None
    """Export path of the Asset (root UDT instance) the point belongs to."""
    source_class: SourceClass
    support: bool
    export_value: Any = None
    """The `value` the export configures, resolved like every other property; None if unset.

    Raw from the export: a Document is a dict, a DataSet a JSON string, a DateTime epoch
    milliseconds, and a UDT parameter binding a `{"bindType": ...}` dict.
    """

    @property
    def alarm_bit(self) -> bool:
        """True for a fault or alarm bit, which is active when true or nonzero."""
        return is_alarm_bit(self.source_class, self.data_type, self.name)


class AssetModel:
    """Read-only registry of the Asset Model. Every mapping iterates in export-path order."""

    def __init__(
        self,
        udt_types: Iterable[UdtType],
        udt_instances: Iterable[UdtInstance],
        points: Iterable[Point],
    ) -> None:
        self.udt_types: Mapping[str, UdtType] = _frozen_index(udt_types, "type_id")
        self.udt_instances: Mapping[str, UdtInstance] = _frozen_index(udt_instances, "path")
        self.assets: Mapping[str, UdtInstance] = MappingProxyType(
            {path: i for path, i in self.udt_instances.items() if i.is_asset}
        )
        self.points: Mapping[str, Point] = _frozen_index(points, "path")
        by_asset: dict[str, list[Point]] = {path: [] for path in self.assets}
        for point in self.points.values():
            if point.asset is not None:
                by_asset[point.asset].append(point)
        self._points_by_asset = {path: tuple(ps) for path, ps in by_asset.items()}

    def point(self, path: str) -> Point:
        return self.points[path]

    def asset(self, path: str) -> UdtInstance:
        return self.assets[path]

    def points_of(self, asset_path: str) -> tuple[Point, ...]:
        """Every point of one Asset, including those of its nested UDT instances."""
        return self._points_by_asset[asset_path]

    def asset_of(self, point_path: str) -> UdtInstance | None:
        asset = self.points[point_path].asset
        return None if asset is None else self.assets[asset]

    def contract_checksum(self) -> str:
        """SHA-256 of the sorted `(path, dataType, typeId)` list: the frozen contract."""
        lines = (f"{p.path}\t{p.data_type}\t{p.type_id or ''}" for p in self.points.values())
        return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def _frozen_index[T](items: Iterable[T], key: str) -> Mapping[str, T]:
    index: dict[str, T] = {}
    for item in items:
        k = getattr(item, key)
        if k in index:
            raise ValueError(f"duplicate {key} in the Asset Model: {k!r}")
        index[k] = item
    return MappingProxyType(dict(sorted(index.items())))
