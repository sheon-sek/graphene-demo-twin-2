"""Projection: world state onto every Asset Model point, one typed value per point per step."""

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType

from graphene_demo_twin.asset_model import AssetModel, SourceClass
from graphene_demo_twin.plant_design import PLANT_VIEWS
from graphene_demo_twin.projection.values import (
    Quality,
    coerce,
    fallback_value,
    type_default,
)
from graphene_demo_twin.sim import Scalar, WorldState


class PointSource(StrEnum):
    """What drives a point's value."""

    PHYSICS = "physics"
    """AssetState of the asset the point belongs to."""
    PLANT_VIEW = "plant_view"
    """AssetState of assets modelled elsewhere, observed through a Plant View."""
    FALLBACK = "fallback"
    """A Compatibility Fallback: constant, outside the modelled world."""


@dataclass(frozen=True, slots=True)
class Binding:
    """Drives one point from world state. `read` returns the raw physical value; the projector
    types it to the point's data type."""

    path: str
    read: Callable[[WorldState], Scalar]


@dataclass(frozen=True, slots=True)
class CoverageEntry:
    path: str
    source: PointSource
    source_class: SourceClass
    support: bool
    debt: bool
    """A fallback on a point that must eventually have a causal source: anything but a
    Support Asset's or static metadata."""


class CoverageReport:
    """Every point's source, in export-path order."""

    def __init__(self, entries: Iterable[CoverageEntry]) -> None:
        self.entries: Mapping[str, CoverageEntry] = MappingProxyType({e.path: e for e in entries})

    def counts(self) -> dict[str, int]:
        counts = {s.value: 0 for s in PointSource}
        for e in self.entries.values():
            counts[e.source.value] += 1
        return counts

    def debt(self) -> int:
        return sum(e.debt for e in self.entries.values())

    def paths(self, source: PointSource) -> tuple[str, ...]:
        return tuple(p for p, e in self.entries.items() if e.source is source)


@dataclass(frozen=True, slots=True)
class Projection:
    """Every point's value and quality at one sim second."""

    time: int
    values: Mapping[str, Scalar]
    degraded: Mapping[str, Quality] = field(default_factory=dict)
    """Quality of the points whose quality is not good; every other point is good."""

    def quality(self, path: str) -> Quality:
        self.values[path]  # unknown paths raise KeyError
        return self.degraded.get(path, Quality.GOOD)


class Projector:
    """Projects WorldState onto the Asset Model. Bound points are recomputed every step; every
    other point keeps its Compatibility Fallback, flagged in the coverage report."""

    def __init__(self, asset_model: AssetModel, bindings: Iterable[Binding]) -> None:
        self._data_types = {p.path: p.data_type for p in asset_model.points.values()}
        self._bindings: dict[str, Binding] = {}
        for b in bindings:
            if b.path not in asset_model.points:
                raise ValueError(f"bound point is not in the Asset Model: {b.path}")
            if b.path in self._bindings:
                raise ValueError(f"point bound twice: {b.path}")
            self._bindings[b.path] = b
        self._template: dict[str, Scalar] = {
            p.path: type_default(p.data_type) if p.path in self._bindings else fallback_value(p)
            for p in asset_model.points.values()
        }
        """Every point in export-path order, holding its fallback, or a slot if bound."""
        self.coverage = CoverageReport(
            _entry(p.path, self._source(p.path), p.source_class, p.support)
            for p in asset_model.points.values()
        )

    def project(self, state: WorldState) -> Projection:
        values = dict(self._template)
        degraded: dict[str, Quality] = {}
        for path, binding in self._bindings.items():
            data_type = self._data_types[path]
            try:
                values[path] = coerce(binding.read(state), data_type)
            except ValueError:
                values[path] = type_default(data_type)
                degraded[path] = Quality.BAD
        return Projection(state.time, MappingProxyType(values), MappingProxyType(degraded))

    def _source(self, path: str) -> PointSource:
        if path not in self._bindings:
            return PointSource.FALLBACK
        if any(path.startswith(f"{view}/") for view in PLANT_VIEWS):
            return PointSource.PLANT_VIEW
        return PointSource.PHYSICS


def _entry(path: str, source: PointSource, cls: SourceClass, support: bool) -> CoverageEntry:
    allowed = support or cls is SourceClass.STATIC_METADATA
    return CoverageEntry(path, source, cls, support, source is PointSource.FALLBACK and not allowed)
