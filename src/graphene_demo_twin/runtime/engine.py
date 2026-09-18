from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import json
import logging
import math
import time
import uuid
from typing import Any

from graphene_demo_twin.config import ROOT, demo_scenarios, world_config
from graphene_demo_twin.domain.model import DomainModel, parse_utc
from graphene_demo_twin.faults.engine import FaultActivation, FaultEngine
from graphene_demo_twin.projection.projector import GrapheneProjector


class RuntimeEngine:
    STATES = {"STOPPED", "STARTING", "RUNNING", "PAUSED", "HOLDING", "STOPPING"}

    def __init__(self):
        self.cfg = world_config()
        generated = ROOT / "config/generated"
        self.manifest = json.loads((generated / "graphene-coverage-manifest.json").read_text())
        self.topology = json.loads((generated / "graphene-instance-topology.json").read_text())
        self.model = DomainModel(self.manifest, self.topology)
        self.projector = GrapheneProjector(self.manifest)
        self.faults = FaultEngine()
        self.overrides = {}
        self.mode = self.cfg["defaultMode"]
        self.state = "STOPPED"
        self.time_scale = self.cfg["demoTimeScale"]
        self.sim_time = parse_utc(self.cfg["demoStartUtc"])
        self._wall_anchor = time.monotonic()
        self._sim_anchor = self.sim_time
        self.events = deque(maxlen=1000)
        self.logs = deque(maxlen=2000)

    def _event(self, typ: str, **ctx):
        row = {
            "id": str(uuid.uuid4()),
            "type": typ,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "simulationTimestamp": self.now().isoformat(),
            "context": ctx,
        }
        self.events.append(row)
        log = {
            "timestamp": row["timestamp"],
            "level": "INFO",
            "source": "runtime",
            "message": typ,
            "context": ctx,
        }
        self.logs.append(log)
        logging.getLogger("graphene_demo_twin.runtime").info("%s %s", typ, ctx)
        return row

    def now(self):
        if self.state == "RUNNING":
            current = self._sim_anchor + timedelta(
                seconds=(time.monotonic() - self._wall_anchor) * self.time_scale
            )
            if self.mode == "demo" and current >= parse_utc(self.cfg["demoEndUtc"]):
                self.state = "HOLDING"
                self.sim_time = parse_utc(self.cfg["demoEndUtc"])
                return self.sim_time
            return current
        return self.sim_time

    def _anchor(self, dt=None):
        self.sim_time = dt or self.now()
        self._sim_anchor = self.sim_time
        self._wall_anchor = time.monotonic()

    def start(self):
        if self.state in {"RUNNING", "PAUSED", "HOLDING"}:
            return
        self.state = "STARTING"
        self._anchor(self.sim_time)
        self.state = "RUNNING"
        self._event("RUNTIME_STARTED")

    def stop(self):
        self._anchor()
        self.state = "STOPPED"
        self._event("RUNTIME_STOPPED")

    def pause(self):
        if self.state == "RUNNING":
            self._anchor()
            self.state = "PAUSED"
            self._event("RUNTIME_PAUSED")

    def resume(self):
        if self.state in {"PAUSED", "HOLDING"}:
            self._anchor(self.sim_time)
            self.state = "RUNNING"
            self._event("RUNTIME_RESUMED")

    def reset(self):
        reset_to = (
            parse_utc(self.cfg["demoStartUtc"])
            if self.mode == "demo"
            else parse_utc(self.cfg["modelEpochUtc"])
        )
        self._anchor(reset_to)
        self.state = "PAUSED"
        self._event("CLOCK_RESET")

    def seek(self, dt):
        self._anchor(parse_utc(dt))
        self._event("CLOCK_SEEK")

    def set_time_scale(self, scale: float):
        if scale <= 0:
            raise ValueError("time scale must be >0")
        self._anchor()
        self.time_scale = float(scale)
        self._event("TIME_SCALE_CHANGED", timeScale=scale)

    def set_mode(self, mode: str):
        if mode not in {"demo", "open_world"}:
            raise ValueError("invalid mode")
        self._anchor()
        self.mode = mode
        self.time_scale = (
            self.cfg["demoTimeScale"] if mode == "demo" else self.cfg["openWorldTimeScale"]
        )
        self._event("MODE_CHANGED", mode=mode)

    def _scenario_severity(self, scenario: dict, dt: datetime, target: str) -> float:
        start = parse_utc(scenario["startLocal"])
        elapsed = max(0.0, (dt - start).total_seconds())
        ramp = float(scenario.get("rampSeconds", 0) or 0)
        if not ramp:
            return 1.0
        if target.lower() in self.model.authoritative_energy_assets:
            step = self.model.energy_step_seconds
            elapsed = math.floor(elapsed / step) * step
        return min(1.0, max(0.0, elapsed / max(1.0, ramp)))

    def scripted_faults(self, dt):
        if self.mode != "demo":
            return []
        out = []
        for scenario in demo_scenarios()["scenarios"]:
            start = parse_utc(scenario["startLocal"])
            end = parse_utc(scenario["endLocal"]) if scenario.get("endLocal") else None
            if dt < start or (end and dt >= end):
                continue
            target = self._resolve_target(scenario)
            severity = self._scenario_severity(scenario, dt, target)
            out.append(
                FaultActivation(
                    scenario["id"],
                    scenario["recipeId"],
                    target,
                    severity,
                    start.isoformat(),
                    source="SCRIPTED",
                    rampSeconds=scenario.get("rampSeconds", 0),
                    durationSeconds=(end - start).total_seconds() if end else None,
                )
            )
        return out

    def scripted_energy_segments(self, dt: datetime) -> list[FaultActivation]:
        """Piecewise-constant physical constraints that can affect authoritative energy."""
        if self.mode != "demo" or not self.model.authoritative_energy_assets:
            return []

        segments: list[FaultActivation] = []
        step = self.model.energy_step_seconds
        for scenario in demo_scenarios()["scenarios"]:
            target = self._resolve_target(scenario)
            if scenario["recipeId"] not in self.faults.PHYSICAL_RECIPES:
                continue

            start = parse_utc(scenario["startLocal"])
            configured_end = (
                parse_utc(scenario["endLocal"]) if scenario.get("endLocal") else None
            )
            end = min(dt, configured_end) if configured_end else dt
            if end <= start:
                continue

            ramp = float(scenario.get("rampSeconds", 0) or 0)
            ramp_end = min(end, start + timedelta(seconds=ramp)) if ramp else start
            cursor = start
            while ramp and cursor < ramp_end:
                right = min(ramp_end, cursor + timedelta(seconds=step))
                severity = self._scenario_severity(scenario, cursor, target)
                segments.append(
                    FaultActivation(
                        scenario["id"],
                        scenario["recipeId"],
                        target,
                        severity,
                        cursor.isoformat(),
                        source="SCRIPTED",
                        durationSeconds=(right - cursor).total_seconds(),
                    )
                )
                cursor = right
            if end > ramp_end:
                segments.append(
                    FaultActivation(
                        scenario["id"],
                        scenario["recipeId"],
                        target,
                        1.0,
                        ramp_end.isoformat(),
                        source="SCRIPTED",
                        durationSeconds=(end - ramp_end).total_seconds(),
                    )
                )
        return segments

    def _resolve_target(self, scenario):
        assets = [
            asset
            for asset in self.topology["assets"]
            if asset["typeId"] == scenario.get("targetType")
        ]
        if scenario.get("targetMatch"):
            match = scenario["targetMatch"].lower()
            hits = [asset for asset in assets if match in asset["exportPath"].lower()]
            if hits:
                return hits[0]["exportPath"]
        index = (
            max(0, min(len(assets) - 1, scenario.get("targetOrdinal", 1) - 1))
            if assets
            else 0
        )
        return assets[index]["exportPath"] if assets else scenario.get("targetType", "")

    def snapshot(self, include_points=True):
        dt = self.now()
        scripted = self.scripted_faults(dt)
        activations = self.faults.activations_at(dt, scripted)
        constraints = self.faults.physical_constraints(activations, self.topology)
        energy_windows = self.faults.physical_constraint_timeline(
            self.model.epoch,
            dt,
            self.topology,
            scripted_segments=self.scripted_energy_segments(dt),
        )
        base = self.model.calculate(
            dt,
            self.mode,
            constraints,
            constraint_windows=energy_windows,
        )
        effective = self.faults.apply(
            base,
            self.manifest,
            scripted,
            self.topology,
            physical_applied=True,
        )
        points = self.projector.project(base, effective, self.overrides) if include_points else None
        ground_truth = None
        if base.world is not None:
            ground_truth = {
                "modeledAssetCount": len(base.world.assets),
                "networkBalance": asdict(base.world.balance),
                "coolingControl": asdict(base.world.cooling_control),
                "authoritativeEnergyAssetCount": len(self.model.authoritative_energy_assets),
            }
        return {
            "status": self.status(),
            "site": effective["site"],
            "faults": effective["faults"],
            "groundTruth": ground_truth,
            "points": points,
        }

    def status(self):
        return {
            "mode": self.mode,
            "state": self.state,
            "simulationTimestamp": self.now().isoformat(),
            "timeScale": self.time_scale,
            "activeInjectedFaults": len(self.faults.active),
            "activeOverrides": len(self.overrides),
        }
