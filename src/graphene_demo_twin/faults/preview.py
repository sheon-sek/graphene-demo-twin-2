"""Fault Preview: a proposed fault run in a What-if Fork against the same fork without it."""

import math
from collections import deque
from dataclasses import dataclass

from graphene_demo_twin.asset_model import AssetModel, SourceClass
from graphene_demo_twin.faults.catalog import (
    INJECT,
    STANDARD_CATALOG,
    FaultCatalog,
    FaultError,
    FaultParams,
)
from graphene_demo_twin.plant_design import PlantDesign
from graphene_demo_twin.projection import Projection, Projector, Quality
from graphene_demo_twin.sim import Event, Scalar, Simulation

PREVIEW_MINUTES = (15, 30, 60)
"""The preview windows the Operator Console offers."""
ALARM_SAMPLE_S = 10
"""How often alarm bits are compared during the run, in sim seconds."""


@dataclass(frozen=True, slots=True)
class AffectedNode:
    """A Plant Design node (asset, Unexported Asset or room) whose state the fault changes."""

    node: str
    first_at: int
    """Sim time of the first step at which its state differed from the world without the
    fault."""
    hops: int | None
    """Distance from the faulted asset along Plant Design connections (and from a room to
    what it contains); None when no path leads there."""


@dataclass(frozen=True, slots=True)
class PointDiff:
    path: str
    node: str | None
    """The Asset the point belongs to, or None for a Plant View point."""
    base: Scalar
    """Value at the end of the preview without the fault."""
    predicted: Scalar
    """Value at the end of the preview with the fault."""
    base_quality: Quality
    predicted_quality: Quality


@dataclass(frozen=True, slots=True)
class AlarmChange:
    path: str
    node: str | None
    base: Scalar
    predicted: Scalar
    first_at: int
    """First sampled sim time at which the bit differed from the world without the fault."""


@dataclass(frozen=True, slots=True)
class FaultPreview:
    target: str
    fault: str
    params: FaultParams
    start: int
    """Sim time the fault would be injected at: the source world's time."""
    end: int
    affected: tuple[AffectedNode, ...]
    """In propagation order: first to change first, nearest first on a tie."""
    diffs: tuple[PointDiff, ...]
    """Every point whose value or quality differs at the end, in propagation order."""
    alarms: tuple[AlarmChange, ...]
    """Alarm bits that differ at any sample, in the order they first changed."""


def preview_fault(
    source: Simulation,
    projector: Projector,
    asset_model: AssetModel,
    target: str,
    fault: str,
    params: FaultParams,
    seconds: int,
    catalog: FaultCatalog = STANDARD_CATALOG,
) -> FaultPreview:
    """Run `fault` on `target` for `seconds` in a fork of `source`, which is left untouched,
    and compare it step by step with a second fork that runs without it.

    Both forks carry every event already in `source`'s log and draw the same noise, so the
    prediction is what `source` will show at `end` if the same fault is injected now.
    """
    event = Event(source.time, INJECT, target, {"fault": fault, **params.as_params()})
    if (reason := catalog.check(event, source.design)) is not None:
        raise FaultError(reason)
    base, faulted = source.fork(), source.fork()
    faulted.schedule(event)

    alarms = [
        p.path
        for p in asset_model.points.values()
        if p.source_class is SourceClass.FAULT_ALARM and p.data_type == "Boolean"
    ]
    pending = {*base.state.assets, target}
    first: dict[str, int] = {}
    alarm_first: dict[str, int] = {}
    for i in range(1, seconds + 1):
        base.step()
        faulted.step()
        b, f = base.state.assets, faulted.state.assets
        for node in [n for n in pending if b.get(n) != f.get(n)]:
            first[node] = faulted.time
            pending.discard(node)
        if i % ALARM_SAMPLE_S == 0 or i == seconds:
            before, after = projector.project(base.state), projector.project(faulted.state)
            for path in alarms:
                if path not in alarm_first and before.values[path] != after.values[path]:
                    alarm_first[path] = faulted.time

    hops = _hops(source.design, target)
    affected = sorted(
        (AffectedNode(n, t, hops.get(n)) for n, t in first.items()),
        key=lambda a: (a.first_at, math.inf if a.hops is None else a.hops, a.node),
    )
    order = {a.node: i for i, a in enumerate(affected)}
    before, after = projector.project(base.state), projector.project(faulted.state)
    diffs = [
        PointDiff(
            path,
            asset_model.point(path).asset,
            before.values[path],
            after.values[path],
            before.quality(path),
            after.quality(path),
        )
        for path in after.values
        if _differs(before, after, path)
    ]
    diffs.sort(key=lambda d: order.get(d.node, len(order)))  # stable: export order within
    changes = sorted(
        (
            AlarmChange(p, asset_model.point(p).asset, before.values[p], after.values[p], t)
            for p, t in alarm_first.items()
        ),
        key=lambda a: (a.first_at, order.get(a.node, len(order)), a.path),
    )
    return FaultPreview(
        target,
        fault,
        params,
        source.time,
        faulted.time,
        tuple(affected),
        tuple(diffs),
        tuple(changes),
    )


def _differs(before: Projection, after: Projection, path: str) -> bool:
    if before.quality(path) is not after.quality(path):
        return True
    a, b = before.values[path], after.values[path]
    if isinstance(a, float) and isinstance(b, float):
        return abs(a - b) > 1e-6 * max(1.0, abs(a), abs(b))
    return a != b


def _hops(design: PlantDesign, origin: str) -> dict[str, int]:
    """Distance from `origin` downstream along every connection kind, where a room also
    reaches the assets inside it."""
    hops = {origin: 0}
    queue = deque([origin])
    while queue:
        node = queue.popleft()
        reached = list(design.downstream(node))
        if design.is_room(node):
            reached += (a.path for a in design.assets_in(node))
        for n in reached:
            if n not in hops:
                hops[n] = hops[node] + 1
                queue.append(n)
    return hops
