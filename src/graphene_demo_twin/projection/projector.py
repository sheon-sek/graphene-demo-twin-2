"""Projection: world state onto every Asset Model point, one typed value per point per step."""

from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
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
class GroupBinding:
    """Drives several points from one reading of the world state, for points that share an
    expensive derivation (a power-flow pass over every meter). `read` returns the raw values
    in the order of `paths`."""

    paths: tuple[str, ...]
    read: Callable[[WorldState], Sequence[Scalar]]


@dataclass(frozen=True, slots=True)
class QualityBinding:
    """Drives the quality of `paths` from world state (communication loss), leaving their
    values to their own bindings."""

    paths: tuple[str, ...]
    read: Callable[[WorldState], Quality]


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

    def __init__(
        self,
        asset_model: AssetModel,
        bindings: Iterable[Binding | GroupBinding],
        quality: Iterable[QualityBinding] = (),
    ) -> None:
        self._data_types = {p.path: p.data_type for p in asset_model.points.values()}
        self._bindings: dict[str, Binding] = {}
        self._groups: list[tuple[GroupBinding, frozenset[str]]] = []
        bound: set[str] = set()
        for b in bindings:
            paths = b.paths if isinstance(b, GroupBinding) else (b.path,)
            for path in paths:
                if path not in asset_model.points:
                    raise ValueError(f"bound point is not in the Asset Model: {path}")
                if path in bound:
                    raise ValueError(f"point bound twice: {path}")
                bound.add(path)
            if isinstance(b, GroupBinding):
                self._groups.append((b, frozenset(b.paths)))
            else:
                self._bindings[b.path] = b
        self._quality = tuple(quality)
        for q in self._quality:
            if missing := [p for p in q.paths if p not in asset_model.points]:
                raise ValueError(f"quality bound to points not in the Asset Model: {missing}")
        self._bound = frozenset(bound)
        self._template: dict[str, Scalar] = {
            p.path: type_default(p.data_type) if p.path in bound else fallback_value(p)
            for p in asset_model.points.values()
        }
        """Every point in export-path order, holding its fallback, or a slot if bound."""
        self.coverage = CoverageReport(
            _entry(p.path, self._source(p.path), p.source_class, p.support)
            for p in asset_model.points.values()
        )

    def project(self, state: WorldState, only: Collection[str] | None = None) -> Projection:
        """Every point's value and quality, or with `only` just those points' (the others keep
        their fallback or type default, and good quality)."""
        values = dict(self._template)
        degraded: dict[str, Quality] = {}
        data_types = self._data_types
        bindings = self._bindings
        if only is not None:
            bindings = {p: bindings[p] for p in only if p in bindings}
        for path, binding in bindings.items():
            try:
                values[path] = coerce(binding.read(state), data_types[path])
            except ValueError:
                values[path] = type_default(data_types[path])
                degraded[path] = Quality.BAD
        for group, members in self._groups:
            if only is not None and members.isdisjoint(only):
                continue
            for path, raw in zip(group.paths, group.read(state), strict=True):
                try:
                    values[path] = coerce(raw, data_types[path])
                except ValueError:
                    values[path] = type_default(data_types[path])
                    degraded[path] = Quality.BAD
        for q in self._quality:
            quality = q.read(state)
            if quality is not Quality.GOOD:
                for path in q.paths:
                    if degraded.get(path) is not Quality.BAD:
                        degraded[path] = quality
        return Projection(state.time, MappingProxyType(values), MappingProxyType(degraded))

    def _source(self, path: str) -> PointSource:
        if path not in self._bound:
            return PointSource.FALLBACK
        if any(path.startswith(f"{view}/") for view in PLANT_VIEWS):
            return PointSource.PLANT_VIEW
        return PointSource.PHYSICS


def _entry(path: str, source: PointSource, cls: SourceClass, support: bool) -> CoverageEntry:
    allowed = support or cls is SourceClass.STATIC_METADATA
    return CoverageEntry(path, source, cls, support, source is PointSource.FALLBACK and not allowed)
