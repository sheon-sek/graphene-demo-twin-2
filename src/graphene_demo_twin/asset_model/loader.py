"""Parse the Ignition export (`reference/graphene/*.json`, read-only) into the Asset Model.

A point's properties come from a chain of definitions, most specific last: the UDT type's
member (after its parent type's), the overrides a nested UDT instance carries inside its
enclosing type, then the overrides on the exported instance itself. A property absent from the
whole chain is at Ignition's default, which the export omits.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from graphene_demo_twin.asset_model.classify import (
    SUPPORT_TYPES,
    classify,
    in_support_scope,
)
from graphene_demo_twin.asset_model.model import AssetModel, Point, UdtInstance, UdtType

REFERENCE_DIR = Path(__file__).resolve().parents[3] / "reference" / "graphene"
INSTANCES_FILE = "real-graphene-demo-tag-instances.json"
DEFINITIONS_FILE = "real-graphene-demo-udt-definitions.json"

IGNITION_DEFAULT_DATA_TYPE = "Int4"
IGNITION_DEFAULT_VALUE_SOURCE = "memory"

type Tag = dict[str, Any]


def load_asset_model(reference_dir: Path = REFERENCE_DIR) -> AssetModel:
    definitions = _read(reference_dir / DEFINITIONS_FILE)
    instances = _read(reference_dir / INSTANCES_FILE)
    types = _TypeLibrary(definitions)
    walker = _InstanceWalker(types)
    walker.walk(instances, prefix="", owner=None)
    return AssetModel(types.udt_types(), walker.udt_instances, walker.points)


def _read(path: Path) -> Tag:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def _join(prefix: str, name: str) -> str:
    return f"{prefix}/{name}" if prefix else name


def _resolve(chain: list[Tag], prop: str) -> Any:
    for definition in reversed(chain):
        if prop in definition:
            return definition[prop]
    return None


@dataclass
class _FlatType:
    members: dict[str, list[Tag]] = field(default_factory=dict)
    """Member path -> definition chain, base first."""
    nested: dict[str, str] = field(default_factory=dict)
    """Nested UDT instance path -> its typeId."""


class _TypeLibrary:
    def __init__(self, definitions: Tag) -> None:
        self._raw: dict[str, Tag] = {}
        self._flat: dict[str, _FlatType] = {}
        self._collect(definitions, prefix="")

    def _collect(self, node: Tag, prefix: str) -> None:
        for child in node.get("tags", []):
            type_id = _join(prefix, child["name"])
            if child["tagType"] == "UdtType":
                self._raw[type_id] = child
            elif child["tagType"] == "Folder":
                self._collect(child, type_id)
            else:
                raise ValueError(f"unexpected {child['tagType']} among UDT types: {type_id}")

    def udt_types(self) -> list[UdtType]:
        return [
            UdtType(
                type_id=type_id,
                parent=raw.get("typeId"),
                members=tuple(sorted(self.flat(type_id).members)),
            )
            for type_id, raw in self._raw.items()
        ]

    def flat(self, type_id: str) -> _FlatType:
        if type_id in self._flat:
            return self._flat[type_id]
        if type_id not in self._raw:
            raise ValueError(f"UDT type not in the export: {type_id!r}")
        raw = self._raw[type_id]
        flat = self._flat[type_id] = _FlatType()
        if parent := raw.get("typeId"):
            inherited = self.flat(parent)
            flat.members = {k: list(v) for k, v in inherited.members.items()}
            flat.nested = dict(inherited.nested)
        self._flatten(raw, "", flat)
        return flat

    def _flatten(self, node: Tag, rel: str, flat: _FlatType) -> None:
        for child in node.get("tags", []):
            key = _join(rel, child["name"])
            match child["tagType"]:
                case "AtomicTag":
                    flat.members.setdefault(key, []).append(child)
                case "UdtInstance":
                    if type_id := child.get("typeId"):
                        inner = self.flat(type_id)
                        for member, chain in inner.members.items():
                            flat.members.setdefault(f"{key}/{member}", []).extend(chain)
                        for member, nested_type in inner.nested.items():
                            flat.nested[f"{key}/{member}"] = nested_type
                        flat.nested[key] = type_id
                    self._flatten(child, key, flat)
                case "Folder":
                    self._flatten(child, key, flat)
                case other:
                    raise ValueError(f"unexpected {other} inside a UDT type: {key}")


@dataclass(frozen=True)
class _Owner:
    """The UDT instances enclosing the node being walked."""

    asset: str
    flat: _FlatType
    instance: str
    type_id: str
    support: bool


class _InstanceWalker:
    def __init__(self, types: _TypeLibrary) -> None:
        self._types = types
        self.udt_instances: list[UdtInstance] = []
        self.points: list[Point] = []

    def walk(self, node: Tag, prefix: str, owner: _Owner | None) -> None:
        for child in node.get("tags", []):
            path = _join(prefix, child["name"])
            match child["tagType"]:
                case "AtomicTag":
                    self.points.append(self._point(child, path, owner))
                case "UdtInstance":
                    self.walk(child, path, self._enter_instance(child, path, owner))
                case "Folder":
                    self.walk(child, path, owner)
                case other:
                    raise ValueError(f"unexpected {other} in the instance export: {path}")

    def _enter_instance(self, tag: Tag, path: str, outer: _Owner | None) -> _Owner:
        if outer is None:
            type_id = tag["typeId"]
            flat = self._types.flat(type_id)
            support = in_support_scope(path) or type_id in SUPPORT_TYPES
            owner = _Owner(asset=path, flat=flat, instance=path, type_id=type_id, support=support)
        else:
            rel = path.removeprefix(f"{outer.asset}/")
            type_id = tag.get("typeId") or outer.flat.nested.get(rel)
            if type_id is None:
                raise ValueError(f"nested UDT instance not in its type: {path}")
            owner = _Owner(outer.asset, outer.flat, path, type_id, outer.support)
        self.udt_instances.append(UdtInstance(path, type_id, owner.asset, owner.support))
        return owner

    def _point(self, tag: Tag, path: str, owner: _Owner | None) -> Point:
        if owner is None:
            member = None
            chain = [tag]
            support = in_support_scope(path)
        else:
            member = path.removeprefix(f"{owner.asset}/")
            if member not in owner.flat.members:
                raise ValueError(f"point is not a member of UDT type {owner.type_id!r}: {path}")
            chain = [*owner.flat.members[member], tag]
            support = owner.support
        data_type = _resolve(chain, "dataType") or IGNITION_DEFAULT_DATA_TYPE
        value_source = _resolve(chain, "valueSource") or IGNITION_DEFAULT_VALUE_SOURCE
        eng_unit = _resolve(chain, "engUnit") or None
        type_id = None if owner is None else owner.type_id
        return Point(
            path=path,
            name=tag["name"],
            member=member,
            data_type=data_type,
            type_id=type_id,
            value_source=value_source,
            eng_unit=eng_unit,
            udt_instance=None if owner is None else owner.instance,
            asset=None if owner is None else owner.asset,
            source_class=classify(
                path=path,
                name=tag["name"],
                type_id=type_id,
                data_type=data_type,
                value_source=value_source,
                eng_unit=eng_unit,
                support=support,
            ),
            support=support,
            export_value=_resolve(chain, "value"),
        )
