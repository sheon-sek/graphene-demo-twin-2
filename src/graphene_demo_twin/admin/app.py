from __future__ import annotations

import asyncio
import json

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from graphene_demo_twin.admin.commands import AdminCommandService
from graphene_demo_twin.config import ROOT, fault_catalog, world_config
from graphene_demo_twin.runtime.engine import RuntimeEngine

app = FastAPI(title="Graphene Virtual World Engine", version="0.1.0")
runtime: RuntimeEngine | None = None
commands: AdminCommandService | None = None


def rt() -> RuntimeEngine:
    global runtime, commands
    if runtime is None:
        runtime = RuntimeEngine()
        commands = AdminCommandService(runtime)
    return runtime


def svc() -> AdminCommandService:
    rt()
    assert commands is not None
    return commands


class Command(BaseModel):
    commandId: str
    confirm: bool = True


class Scale(Command):
    value: float = Field(gt=0, le=3600)


class Seek(Command):
    timestamp: str


class Mode(Command):
    mode: str


class FaultIn(Command):
    recipeId: str
    targetAsset: str
    severity: float = Field(ge=0, le=1)


class FaultUpdate(Command):
    severity: float = Field(ge=0, le=1)


class OverrideIn(Command):
    exportPath: str
    value: object


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/status")
def status():
    return rt().status()


@app.get("/api/config")
def config():
    return world_config()


@app.get("/api/topology")
def topology():
    return rt().topology


@app.get("/api/schema/coverage")
def coverage():
    manifest = rt().manifest
    return {
        "sourceCounts": manifest["sourceCounts"],
        "exportedPointCount": manifest["exportedPointCount"],
        "extensionPointCount": manifest["extensionPointCount"],
        "totalPointCount": len(manifest["points"]),
    }


@app.get("/api/assets")
def assets():
    return {"assets": rt().topology["assets"]}


@app.get("/api/snapshot")
def snapshot(points: bool = True):
    return rt().snapshot(points)


@app.get("/api/faults/catalog")
def catalog():
    return fault_catalog()


@app.get("/api/faults/active")
def active_faults():
    return {"faults": rt().faults.list()}


@app.get("/api/overrides/active")
def active_overrides():
    return {"overrides": rt().overrides}


@app.get("/api/events")
def events():
    return {"events": list(rt().events)}


@app.get("/api/logs")
def logs():
    return {"logs": list(rt().logs)}


def mutate(cmd: Command, action: str, fn):
    try:
        return svc().execute(cmd.commandId, cmd.confirm, action, fn)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/api/runtime/start")
def start(c: Command):
    return mutate(c, "start", rt().start)


@app.post("/api/runtime/stop")
def stop(c: Command):
    return mutate(c, "stop", rt().stop)


@app.post("/api/runtime/pause")
def pause(c: Command):
    return mutate(c, "pause", rt().pause)


@app.post("/api/runtime/resume")
def resume(c: Command):
    return mutate(c, "resume", rt().resume)


@app.post("/api/runtime/reset")
def reset(c: Command):
    return mutate(c, "reset", rt().reset)


@app.post("/api/runtime/seek")
def seek(c: Seek):
    return mutate(c, "seek", lambda: rt().seek(c.timestamp))


@app.post("/api/runtime/time-scale")
def scale(c: Scale):
    return mutate(c, "set-time-scale", lambda: rt().set_time_scale(c.value))


@app.post("/api/runtime/mode")
def mode(c: Mode):
    return mutate(c, "set-mode", lambda: rt().set_mode(c.mode))


@app.post("/api/faults/inject")
def inject(c: FaultIn):
    def do():
        valid_ids = {r["id"] for r in fault_catalog().get("recipes", [])}
        if c.recipeId not in valid_ids:
            raise ValueError(f"unknown recipeId: {c.recipeId!r}; valid: {sorted(valid_ids)}")
        fault = rt().faults.inject(c.recipeId, c.targetAsset, c.severity, rt().now())
        rt()._event(
            "FAULT_INJECTED",
            injectionId=fault.injectionId,
            recipeId=fault.recipeId,
        )
        return {"injectionId": fault.injectionId}

    return mutate(c, "inject-fault", do)


@app.put("/api/faults/{injection_id}")
def update_fault(injection_id: str, c: FaultUpdate):
    def do():
        rt().faults.update(injection_id, c.severity, rt().now())
        rt()._event("FAULT_UPDATED", injectionId=injection_id, severity=c.severity)

    return mutate(c, "update-fault", do)


@app.delete("/api/faults/{injection_id}")
def clear_fault(injection_id: str, commandId: str, confirm: bool = True):
    def do():
        cleared = rt().faults.clear(injection_id, rt().now())
        rt()._event("FAULT_CLEARED", injectionId=injection_id)
        return {"injectionId": injection_id, "cleared": cleared is not None}

    return mutate(
        Command(commandId=commandId, confirm=confirm),
        "clear-fault",
        do,
    )


@app.delete("/api/faults")
def clear_faults(commandId: str, confirm: bool = True):
    def do():
        cleared = rt().faults.clear_all(rt().now())
        rt()._event("FAULTS_CLEARED")
        return {"clearedInjectionIds": [fault.injectionId for fault in cleared]}

    return mutate(
        Command(commandId=commandId, confirm=confirm),
        "clear-all-faults",
        do,
    )


@app.delete("/api/overrides")
def clear_overrides(commandId: str, confirm: bool = True):
    return mutate(
        Command(commandId=commandId, confirm=confirm),
        "clear-all-overrides",
        lambda: (rt().overrides.clear(), rt()._event("OVERRIDES_CLEARED")),
    )


@app.post("/api/overrides")
def override(c: OverrideIn):
    def do():
        if c.exportPath not in {p["exportPath"] for p in rt().manifest["points"]}:
            raise ValueError("unknown exportPath")
        rt().overrides[c.exportPath] = c.value
        rt()._event("OVERRIDE_APPLIED", exportPath=c.exportPath)

    return mutate(c, "apply-override", do)


@app.delete("/api/overrides/{path:path}")
def clear_override(path: str, commandId: str, confirm: bool = True):
    return mutate(
        Command(commandId=commandId, confirm=confirm),
        "clear-override",
        lambda: (
            rt().overrides.pop(path, None),
            rt()._event("OVERRIDE_CLEARED", exportPath=path),
        ),
    )


@app.get("/api/stream")
async def stream(request: Request):
    async def gen():
        while True:
            if await request.is_disconnected():
                break
            payload = rt().snapshot(False)
            yield f"event: telemetry\ndata: {json.dumps(payload, default=str)}\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )


web_dist = ROOT / "apps/web/dist"
if web_dist.exists():
    app.mount("/assets", StaticFiles(directory=web_dist / "assets"), name="assets")

    @app.get("/{path:path}")
    def spa(path: str):
        return FileResponse(web_dist / "index.html")
