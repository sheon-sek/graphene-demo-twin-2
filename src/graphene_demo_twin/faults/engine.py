from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
import uuid

from graphene_demo_twin.domain.energy import ConstraintWindow


@dataclass
class FaultActivation:
    injectionId: str
    recipeId: str
    targetAsset: str
    severity: float
    simulationTimestamp: str
    source: str = "INJECTED"
    rampSeconds: float = 0
    durationSeconds: float | None = None
    parameters: dict[str, Any] | None = None


class FaultEngine:
    PHYSICAL_RECIPES = {
        "COOLING_TOWER_FAILURE",
        "CHILLER_CONDENSER_DEGRADATION",
        "CRAC_VALVE_STUCK",
        "PAHU_AFTER_HOURS",
    }

    def __init__(self):
        self.active: dict[str, FaultActivation] = {}
        self.history: list[FaultActivation] = []

    @staticmethod
    def _utc(value: str | datetime) -> datetime:
        if isinstance(value, datetime):
            dt = value
        else:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            raise ValueError("fault timestamp must be timezone-aware")
        return dt.astimezone(timezone.utc)

    @classmethod
    def _start(cls, fault: FaultActivation) -> datetime:
        return cls._utc(fault.simulationTimestamp)

    @classmethod
    def _end(cls, fault: FaultActivation) -> datetime | None:
        if fault.durationSeconds is None:
            return None
        return cls._start(fault) + timedelta(seconds=max(0.0, fault.durationSeconds))

    @classmethod
    def _active_at(cls, fault: FaultActivation, ts: datetime) -> bool:
        start = cls._start(fault)
        end = cls._end(fault)
        return start <= ts and (end is None or ts < end)

    def inject(
        self,
        recipe_id: str,
        target_asset: str,
        severity: float,
        ts: datetime,
        **kwargs,
    ) -> FaultActivation:
        if not 0 <= severity <= 1:
            raise ValueError("severity must be 0..1")
        fault = FaultActivation(
            str(uuid.uuid4()),
            recipe_id,
            target_asset,
            severity,
            ts.astimezone(timezone.utc).isoformat(),
            parameters=kwargs or {},
        )
        self.active[fault.injectionId] = fault
        self.history.append(fault)
        return fault

    def update(
        self,
        injection_id: str,
        severity: float,
        ts: datetime | None = None,
    ) -> FaultActivation:
        if not 0 <= severity <= 1:
            raise ValueError("severity must be 0..1")
        if injection_id not in self.active:
            raise KeyError(injection_id)

        current = self.active[injection_id]
        if ts is None:
            current.severity = severity
            return current

        changed_at = ts.astimezone(timezone.utc)
        start = self._start(current)
        if changed_at < start:
            raise ValueError("fault update timestamp precedes activation")
        current.durationSeconds = (changed_at - start).total_seconds()
        replacement = FaultActivation(
            injectionId=current.injectionId,
            recipeId=current.recipeId,
            targetAsset=current.targetAsset,
            severity=severity,
            simulationTimestamp=changed_at.isoformat(),
            source=current.source,
            rampSeconds=0,
            parameters=dict(current.parameters or {}),
        )
        self.active[injection_id] = replacement
        self.history.append(replacement)
        return replacement

    def clear(self, injection_id: str, ts: datetime | None = None):
        current = self.active.pop(injection_id, None)
        if current is None:
            return None
        if ts is None:
            self.history = [fault for fault in self.history if fault is not current]
            return current

        cleared_at = ts.astimezone(timezone.utc)
        start = self._start(current)
        if cleared_at < start:
            raise ValueError("fault clear timestamp precedes activation")
        current.durationSeconds = (cleared_at - start).total_seconds()
        return current

    def clear_all(self, ts: datetime | None = None):
        cleared = []
        for injection_id in list(self.active):
            fault = self.clear(injection_id, ts)
            if fault is not None:
                cleared.append(fault)
        return cleared

    def list(self):
        return [asdict(value) for value in self.active.values()]

    def activations(self, scripted: list[FaultActivation] | None = None) -> list[FaultActivation]:
        return list(self.active.values()) + list(scripted or [])

    def activations_at(
        self,
        ts: datetime,
        scripted: list[FaultActivation] | None = None,
    ) -> list[FaultActivation]:
        at = ts.astimezone(timezone.utc)
        injected = [fault for fault in self.history if self._active_at(fault, at)]
        return injected + list(scripted or [])

    @staticmethod
    def _resolve_asset_path(target: str, topology: dict | None) -> str:
        if not topology:
            return target
        exact = next(
            (
                asset["exportPath"]
                for asset in topology.get("assets", [])
                if asset["exportPath"].lower() == target.lower()
            ),
            None,
        )
        if exact:
            return exact
        matches = [
            asset["exportPath"]
            for asset in topology.get("assets", [])
            if target.lower() in asset["exportPath"].lower()
        ]
        return matches[0] if len(matches) == 1 else target

    def physical_constraints(
        self,
        activations: list[FaultActivation],
        topology: dict | None = None,
    ) -> dict[str, dict[str, float]]:
        """Translate physical faults into solver constraints before point projection."""
        constraints: dict[str, dict[str, float]] = {}
        for fault in activations:
            if fault.recipeId not in self.PHYSICAL_RECIPES:
                continue
            severity = max(0.0, min(1.0, fault.severity))
            target = self._resolve_asset_path(fault.targetAsset, topology)
            row = constraints.setdefault(target.lower(), {})
            if fault.recipeId == "COOLING_TOWER_FAILURE":
                row["availability"] = min(row.get("availability", 1.0), 1.0 - severity)
            elif fault.recipeId == "CHILLER_CONDENSER_DEGRADATION":
                row["condenser_degradation"] = max(
                    row.get("condenser_degradation", 0.0), severity
                )
            elif fault.recipeId == "CRAC_VALVE_STUCK":
                row["valve_command"] = max(
                    row.get("valve_command", 0.72), 0.62 + 0.20 * severity
                )
                row["valve_position"] = min(
                    row.get("valve_position", 1.0), 1.0 - 0.97 * severity
                )
            elif fault.recipeId == "PAHU_AFTER_HOURS":
                row["after_hours_operation"] = max(
                    row.get("after_hours_operation", 0.0), severity
                )
        return constraints

    def physical_constraint_timeline(
        self,
        start: datetime,
        end: datetime,
        topology: dict | None = None,
        target_paths: set[str] | None = None,
        scripted_segments: list[FaultActivation] | None = None,
    ) -> list[ConstraintWindow]:
        """Return non-overlapping piecewise-constant physical constraints for integration."""
        start = start.astimezone(timezone.utc)
        end = end.astimezone(timezone.utc)
        if end <= start:
            return []

        targets = {path.lower() for path in target_paths} if target_paths else None
        candidates: list[FaultActivation] = []
        for fault in self.history + list(scripted_segments or []):
            if fault.recipeId not in self.PHYSICAL_RECIPES:
                continue
            resolved = self._resolve_asset_path(fault.targetAsset, topology).lower()
            if targets is not None and resolved not in targets:
                continue
            fault_start = self._start(fault)
            fault_end = self._end(fault) or end
            if fault_start < end and fault_end > start:
                candidates.append(fault)

        if not candidates:
            return []

        boundaries = {start, end}
        for fault in candidates:
            boundaries.add(max(start, self._start(fault)))
            boundaries.add(min(end, self._end(fault) or end))
        ordered = sorted(boundaries)

        windows: list[ConstraintWindow] = []
        for left, right in zip(ordered, ordered[1:]):
            if right <= left:
                continue
            midpoint = left + (right - left) / 2
            active = [fault for fault in candidates if self._active_at(fault, midpoint)]
            constraints = self.physical_constraints(active, topology)
            if not constraints:
                continue
            if windows and windows[-1].end == left and windows[-1].constraints == constraints:
                previous = windows.pop()
                windows.append(ConstraintWindow(previous.start, right, constraints))
            else:
                windows.append(ConstraintWindow(left, right, constraints))
        return windows

    def _network_targets(self, target: str, topology: dict | None) -> set[str]:
        targets = {target}
        if not topology:
            return targets
        assets = topology.get("assets", [])
        by_id = {asset["assetId"]: asset for asset in assets}
        switch = next(
            (asset for asset in assets if asset["exportPath"].lower() == target.lower()),
            None,
        )
        if not switch:
            return targets
        for relation in topology.get("relations", []):
            if (
                relation.get("kind") == "networkParent"
                and relation.get("from") == switch["assetId"]
                and relation.get("to") in by_id
            ):
                targets.add(by_id[relation["to"]]["exportPath"])
        return targets

    def apply(
        self,
        snapshot,
        manifest: dict,
        scripted: list[FaultActivation] | None = None,
        topology: dict | None = None,
        physical_applied: bool = False,
    ):
        values = dict(snapshot.signals)
        quality = {point["signalKey"]: "Good" for point in manifest["points"]}
        site = dict(snapshot.site)
        activations = self.activations_at(snapshot.timestamp, scripted)
        points = manifest["points"]
        for fault in activations:
            severity = max(0, min(1, fault.severity))
            target = fault.targetAsset.lower()
            match_targets = {target}
            if fault.recipeId == "NETWORK_DEVICE_FAILURE":
                match_targets = {
                    item.lower() for item in self._network_targets(fault.targetAsset, topology)
                }
            matching = [
                point
                for point in points
                if any(
                    match_target and match_target in point["exportPath"].lower()
                    for match_target in match_targets
                )
            ]
            by_member = {point["memberName"].lower(): point for point in matching}

            if fault.recipeId == "CRAC_VALVE_STUCK":
                if physical_applied:
                    continue
                for point in matching:
                    name = point["memberName"].lower()
                    key = point["signalKey"]
                    if "valve command" in name:
                        values[key] = 62 + (82 - 62) * severity
                    elif "valve feedback" in name:
                        values[key] = 62 + (3 - 62) * severity
                    elif "chw flow" in name:
                        values[key] = 8.5 + (0.3 - 8.5) * severity
                    elif "supply air temperature" in name:
                        values[key] = 14.4 + (22.4 - 14.4) * severity
                    elif "filter choke alarm" in name:
                        values[key] = False
                site["hallATempC"] = round(site.get("hallATempC", 23.1) + 1.1 * severity, 3)
            elif fault.recipeId == "CHILLER_CONDENSER_DEGRADATION":
                if physical_applied:
                    continue
                cop = 4.44 + (3.89 - 4.44) * severity
                input_power = None
                for point in matching:
                    name = point["memberName"].lower()
                    key = point["signalKey"]
                    if name == "cop":
                        values[key] = cop
                    elif "kw per rt" in name:
                        values[key] = 3.517 / cop
                    elif "cw flow" in name:
                        values[key] = max(0.1, float(values[key]) * (1 - 0.28 * severity))
                    elif "cw approach" in name:
                        values[key] = 3.2 + (5.5 - 3.2) * severity
                    elif "condenser pressure" in name:
                        values[key] = float(values[key]) * (1 + 0.16 * severity)
                    elif name == "input power":
                        values[key] = float(values[key]) * (1 + 0.14 * severity)
                        input_power = float(values[key])
                if input_power is None and "input power" in by_member:
                    input_power = float(values[by_member["input power"]["signalKey"]])
                if input_power is not None and "cooling output" in by_member:
                    values[by_member["cooling output"]["signalKey"]] = input_power * cop
            elif fault.recipeId == "COOLING_TOWER_FAILURE":
                if physical_applied:
                    continue
                for point in matching:
                    name = point["memberName"].lower()
                    key = point["signalKey"]
                    if any(
                        token in name for token in ("power", "frequency", "current", "fan speed", "flow")
                    ):
                        values[key] = 0.0
                    elif name in {"on_off", "on off"}:
                        values[key] = 0
                    elif "fault" in name:
                        values[key] = True
            elif fault.recipeId == "UPS_RECTIFIER_FAULT":
                for point in matching:
                    name = point["memberName"].lower()
                    if (
                        name
                        in {"rectifier failure", "battery charging failure", "ups common alarm"}
                        or "rectifier failure" in name
                    ):
                        values[point["signalKey"]] = True
            elif fault.recipeId == "NETWORK_DEVICE_FAILURE":
                for point in matching:
                    name = point["memberName"].lower()
                    key = point["signalKey"]
                    if any(token in name for token in ("comm", "status", "link status", "display status")):
                        values[key] = 0
                    elif "ping" in name:
                        values[key] = 9999.0
                        quality[key] = "Bad_CommunicationError"
                    elif any(token in name for token in ("utilization", "speed")):
                        values[key] = 0
            elif fault.recipeId == "WATER_LEAK":
                for point in matching:
                    name = point["memberName"].lower()
                    if "status" in name:
                        values[point["signalKey"]] = 1
                    elif "leak position" in name:
                        values[point["signalKey"]] = 42.0
                site["hallBRhPct"] = round(site.get("hallBRhPct", 52) + 8 * severity, 3)
            elif fault.recipeId == "PAHU_AFTER_HOURS":
                if physical_applied:
                    continue
                for point in matching:
                    name = point["memberName"].lower()
                    if name in {"on_off", "fan on_off", "fan on off"}:
                        values[point["signalKey"]] = 1
                    elif "fan speed" in name:
                        values[point["signalKey"]] = max(
                            float(values[point["signalKey"]]), 58 + 12 * severity
                        )
        return {
            "values": values,
            "quality": quality,
            "faults": [asdict(item) for item in activations],
            "site": site,
        }
