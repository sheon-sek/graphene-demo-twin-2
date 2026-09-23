"""The REST and SSE surfaces, and the built Operator Console, as one FastAPI app.

Everything read here comes from the twin's latest Frame (or a What-if Fork), never from the
live state directly, so REST, SSE and OPC UA always agree on the sim second they show.
"""

import asyncio
import json
from collections.abc import AsyncIterator, Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import FastAPI, HTTPException, Query, status
from fastapi.responses import HTMLResponse
from fastapi.sse import EventSourceResponse, ServerSentEvent
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from graphene_demo_twin.plant_design import ConnectionKind
from graphene_demo_twin.projection import PointSource, Projection
from graphene_demo_twin.sim import Event, EventError, WorldState
from graphene_demo_twin.twin import ForkSession, Frame, Twin

CONSOLE_DIR = Path(__file__).resolve().parents[3] / "apps" / "web" / "dist"
MAX_FORK_ADVANCE_S = 86_400
"""Longest a single fork advance may run ahead, in sim seconds."""


class EventIn(BaseModel):
    kind: str
    target: str
    params: dict[str, Any] = Field(default_factory=dict)


class ForkEventIn(EventIn):
    at: int | None = None
    """Sim time; defaults to the fork's current time."""


class AdvanceIn(BaseModel):
    seconds: int = Field(ge=1, le=MAX_FORK_ADVANCE_S)


class ResetIn(BaseModel):
    confirm: Literal[True]
    """Reset discards the Event Log, so the caller must say so."""


PathsQuery = Annotated[list[str] | None, Query()]


def create_app(twin: Twin, console_dir: Path | None = CONSOLE_DIR) -> FastAPI:
    app = FastAPI(title="Graphene Demo Twin", version="0.2.0")
    asset_model, design, coverage = twin.asset_model, twin.design, twin.projector.coverage

    def point_json(path: str, projection: Projection) -> dict[str, Any]:
        point = asset_model.point(path)
        return {
            "path": path,
            "value": projection.values[path],
            "quality": projection.quality(path).value,
            "timestamp": iso(projection.time),
            "dataType": point.data_type,
            "source": coverage.entries[path].source.value,
        }

    def points_json(
        projection: Projection, paths: list[str] | None, prefix: str | None, asset: str | None
    ) -> dict[str, Any]:
        if paths:
            missing = [p for p in paths if p not in asset_model.points]
            if missing:
                raise HTTPException(404, f"unknown points: {missing}")
            selected: Iterable[str] = paths
        elif asset is not None:
            if asset not in asset_model.assets:
                raise HTTPException(404, f"unknown asset: {asset}")
            selected = (p.path for p in asset_model.points_of(asset))
        else:
            selected = asset_model.points
        if prefix:
            selected = (p for p in selected if p.startswith(prefix))
        return {
            "time": projection.time,
            "timestamp": iso(projection.time),
            "points": [point_json(p, projection) for p in selected],
        }

    def asset_json(path: str) -> dict[str, Any]:
        asset = asset_model.asset(path)
        placed = design.assets.get(path)
        room = None if placed is None else placed.room
        return {
            "path": path,
            "typeId": asset.type_id,
            "support": asset.support,
            "room": room,
            "floor": None if room is None else design.room(room).floor,
            "system": None if placed is None else placed.system,
            "role": None if placed is None else placed.role,
            "position": None if placed is None or placed.x is None else [placed.x, placed.y],
            "pointCount": len(asset_model.points_of(path)),
        }

    def neighbours(node: str, walk: str) -> dict[str, list[str]]:
        if node not in design.assets:
            return {}
        found = {k.value: list(getattr(design, walk)(node, k)) for k in ConnectionKind}
        return {kind: nodes for kind, nodes in found.items() if nodes}

    def fork_json(session: ForkSession) -> dict[str, Any]:
        fork = session.fork
        return {
            "id": session.id,
            "forkedAt": fork.forked_at,
            "time": fork.time,
            "timestamp": iso(fork.time),
            "events": [event_json(e) for e in fork.events],
        }

    def fork_session(fork_id: str) -> ForkSession:
        try:
            return twin.forks.get(fork_id)
        except KeyError:
            raise HTTPException(404, f"unknown fork: {fork_id}") from None

    @app.get("/api/status")
    def get_status() -> dict[str, Any]:
        frame = twin.frame
        return {
            "time": frame.time,
            "timestamp": iso(frame.time),
            "seq": frame.seq,
            "epoch": frame.epoch,
            "seed": twin.live.seed,
            "events": frame.event_count,
            "forks": len(twin.forks),
            "coverage": coverage_json(),
        }

    @app.get("/api/assets")
    def list_assets() -> list[dict[str, Any]]:
        return [asset_json(path) for path in asset_model.assets]

    @app.get("/api/assets/{path:path}")
    def get_asset(path: str) -> dict[str, Any]:
        if path not in asset_model.assets:
            raise HTTPException(404, f"unknown asset: {path}")
        projection = twin.frame.projection
        return {
            **asset_json(path),
            "points": [point_json(p.path, projection) for p in asset_model.points_of(path)],
            "upstream": neighbours(path, "upstream"),
            "downstream": neighbours(path, "downstream"),
        }

    @app.get("/api/points")
    def list_points(
        path: PathsQuery = None, prefix: str | None = None, asset: str | None = None
    ) -> dict[str, Any]:
        return points_json(twin.frame.projection, path, prefix, asset)

    @app.get("/api/points/{path:path}")
    def get_point(path: str) -> dict[str, Any]:
        if path not in asset_model.points:
            raise HTTPException(404, f"unknown point: {path}")
        point, entry = asset_model.point(path), coverage.entries[path]
        return {
            **point_json(path, twin.frame.projection),
            "name": point.name,
            "member": point.member,
            "typeId": point.type_id,
            "asset": point.asset,
            "engUnit": point.eng_unit,
            "sourceClass": point.source_class.value,
            "support": point.support,
            "debt": entry.debt,
        }

    @app.get("/api/state")
    def get_state() -> dict[str, Any]:
        return state_json(twin.frame.state)

    @app.get("/api/state/{node:path}")
    def get_node_state(node: str) -> dict[str, Any]:
        state = twin.frame.state
        if node not in state.assets:
            raise HTTPException(404, f"no AssetState for {node}")
        return {"time": state.time, "node": node, "state": state.assets[node]}

    @app.get("/api/plant-design")
    def get_plant_design() -> dict[str, Any]:
        return {
            "version": design.version,
            "floors": [{"name": f.name, "index": f.index} for f in design.floors],
            "rooms": [
                {
                    "id": r.id,
                    "floor": r.floor,
                    "name": r.name,
                    "x": r.x,
                    "y": r.y,
                    "w": r.w,
                    "h": r.h,
                    "kind": r.kind,
                    "fireZone": r.fire_zone,
                    "outdoor": r.outdoor,
                }
                for r in design.rooms.values()
            ],
            "assets": [
                {
                    "path": a.path,
                    "typeId": a.type_id,
                    "room": a.room,
                    "x": a.x,
                    "y": a.y,
                    "role": a.role,
                    "system": a.system,
                    "unexported": a.unexported,
                    "support": a.support,
                }
                for a in design.assets.values()
            ],
            "unexported": [
                {"id": u.id, "name": u.name, "typeId": u.type_id, "observedBy": list(u.observed_by)}
                for u in design.unexported.values()
            ],
            "connections": [
                {"kind": c.kind.value, "source": c.source, "target": c.target, "label": c.label}
                for c in design.connections
            ],
            "shafts": [
                {
                    "id": s.id,
                    "name": s.name,
                    "x": s.x,
                    "y": s.y,
                    "floors": list(s.floors),
                    "carries": [k.value for k in s.carries],
                }
                for s in design.shafts.values()
            ],
        }

    @app.get("/api/events")
    def list_events() -> dict[str, Any]:
        frame = twin.frame
        return {"time": frame.time, "events": [event_json(e) for e in frame.events]}

    @app.post("/api/events", status_code=status.HTTP_201_CREATED)
    async def submit_event(body: EventIn) -> dict[str, Any]:
        try:
            return event_json(twin.submit(body.kind, body.target, body.params))
        except EventError as e:
            raise HTTPException(422, str(e)) from None

    @app.post("/api/reset")
    async def reset(body: ResetIn) -> dict[str, Any]:
        frame = twin.reset()
        return {"time": frame.time, "seq": frame.seq, "epoch": frame.epoch}

    @app.get("/api/coverage")
    def get_coverage() -> dict[str, Any]:
        return coverage_json()

    @app.get("/api/coverage/points")
    def list_coverage(
        source: PointSource | None = None, debt: bool | None = None
    ) -> list[dict[str, Any]]:
        return [
            {
                "path": e.path,
                "source": e.source.value,
                "sourceClass": e.source_class.value,
                "alarmBit": asset_model.point(e.path).alarm_bit,
                "support": e.support,
                "debt": e.debt,
            }
            for e in coverage.entries.values()
            if (source is None or e.source is source) and (debt is None or e.debt == debt)
        ]

    def coverage_json() -> dict[str, Any]:
        return {
            "total": len(coverage.entries),
            "counts": coverage.counts(),
            "debt": coverage.debt(),
        }

    @app.get("/api/forks")
    def list_forks() -> list[dict[str, Any]]:
        return [fork_json(s) for s in twin.forks]

    @app.post("/api/forks", status_code=status.HTTP_201_CREATED)
    async def create_fork() -> dict[str, Any]:
        return fork_json(twin.forks.create())

    @app.get("/api/forks/{fork_id}")
    def get_fork(fork_id: str) -> dict[str, Any]:
        return fork_json(fork_session(fork_id))

    @app.delete("/api/forks/{fork_id}", status_code=status.HTTP_204_NO_CONTENT)
    def delete_fork(fork_id: str) -> None:
        fork_session(fork_id)
        twin.forks.delete(fork_id)

    @app.post("/api/forks/{fork_id}/advance")
    async def advance_fork(fork_id: str, body: AdvanceIn) -> dict[str, Any]:
        session = fork_session(fork_id)

        def advance() -> None:
            with session.lock:
                session.fork.run_for(body.seconds)

        await asyncio.to_thread(advance)
        return fork_json(session)

    @app.post("/api/forks/{fork_id}/events", status_code=status.HTTP_201_CREATED)
    def schedule_fork_event(fork_id: str, body: ForkEventIn) -> dict[str, Any]:
        session = fork_session(fork_id)
        with session.lock:
            at = session.fork.time if body.at is None else body.at
            try:
                return event_json(
                    session.fork.schedule(Event(at, body.kind, body.target, body.params))
                )
            except EventError as e:
                raise HTTPException(422, str(e)) from None

    @app.get("/api/forks/{fork_id}/points")
    def list_fork_points(
        fork_id: str,
        path: PathsQuery = None,
        prefix: str | None = None,
        asset: str | None = None,
    ) -> dict[str, Any]:
        session = fork_session(fork_id)
        with session.lock:
            projection = twin.projector.project(session.fork.state)
        return points_json(projection, path, prefix, asset)

    @app.get("/api/forks/{fork_id}/state")
    def get_fork_state(fork_id: str) -> dict[str, Any]:
        session = fork_session(fork_id)
        with session.lock:
            return state_json(session.fork.state.copy())

    @app.get("/api/stream", response_class=EventSourceResponse)
    async def stream() -> AsyncIterator[ServerSentEvent]:
        """1 Hz frames: a full `snapshot` first and after every Reset, `delta` otherwise."""
        sent = twin.frame
        yield frame_event("snapshot", sent, None)
        while (frame := await twin.next_frame(sent.seq)) is not None:
            if frame.epoch != sent.epoch:
                yield frame_event("snapshot", frame, None)
            else:
                yield frame_event("delta", frame, sent)
            sent = frame

    if console_dir is not None and (console_dir / "index.html").is_file():
        app.mount("/", StaticFiles(directory=console_dir, html=True), name="console")
    else:

        @app.get("/", response_class=HTMLResponse)
        def console_placeholder() -> str:
            return (
                "<!doctype html><title>Graphene Demo Twin</title>"
                "<p>The Operator Console is not built. Build <code>apps/web</code>, "
                "or browse the API at <a href='/docs'>/docs</a>.</p>"
            )

    return app


def iso(sim_time: int) -> str:
    return datetime.fromtimestamp(sim_time, UTC).isoformat().replace("+00:00", "Z")


def event_json(event: Event) -> dict[str, Any]:
    return {
        "at": event.at,
        "timestamp": iso(event.at),
        "kind": event.kind,
        "target": event.target,
        "params": dict(event.params),
    }


def state_json(state: WorldState) -> dict[str, Any]:
    return {"time": state.time, "timestamp": iso(state.time), "assets": state.assets}


def frame_event(kind: str, frame: Frame, previous: Frame | None) -> ServerSentEvent:
    """A frame as an SSE event; a delta holds only what changed since `previous`."""
    projection = frame.projection
    if previous is None:
        paths: Iterable[str] = projection.values
        state: Mapping[str, Mapping[str, Any]] = frame.state.assets
    else:
        before = previous.projection
        paths = [
            p
            for p, v in projection.values.items()
            if v != before.values[p] or projection.quality(p) is not before.quality(p)
        ]
        state = _state_delta(previous.state, frame.state)
    payload = {
        "seq": frame.seq,
        "epoch": frame.epoch,
        "time": frame.time,
        "timestamp": iso(frame.time),
        "events": frame.event_count,
        "points": {
            p: {"value": projection.values[p], "quality": projection.quality(p).value}
            for p in paths
        },
        "state": state,
    }
    return ServerSentEvent(event=kind, id=str(frame.seq), raw_data=json.dumps(payload))


def _state_delta(before: WorldState, after: WorldState) -> dict[str, dict[str, Any]]:
    delta: dict[str, dict[str, Any]] = {}
    for node, variables in after.assets.items():
        old = before.assets.get(node, {})
        changed = {k: v for k, v in variables.items() if old.get(k, _MISSING) != v}
        if changed:
            delta[node] = changed
    return delta


_MISSING = object()
