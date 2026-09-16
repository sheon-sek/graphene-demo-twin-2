from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable

RUNTIME_CLASSES = {
    "PHYSICAL_SOURCE", "PHYSICAL_DERIVED", "CONTROL_COMMAND", "CONTROL_FEEDBACK",
    "EQUIPMENT_STATE", "FAULT_STATE", "ELECTRICAL_DERIVED", "ENERGY_INTEGRAL",
    "ENVIRONMENT", "NETWORK_STATE", "AGGREGATE", "PRESENTATION_DERIVED",
}
SUPPORT_ROOTS = {"DemoRack", "MQTT Tags", "PredictionCache", "Testing"}
TEST_ROOTS = {"Testing"}


def _walk(nodes: Iterable[dict], parent: str = ""):
    for node in nodes or []:
        path = f"{parent}/{node.get('name', '')}".strip("/")
        yield node, path
        yield from _walk(node.get("tags", []), path)


def _slug(value: str) -> str:
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9]+", "_", value).strip("_")
    return value or "signal"


def stable_key(path: str) -> str:
    return f"sig.{_slug(path)}.{hashlib.sha1(path.encode()).hexdigest()[:8]}"


def infer_type(value: Any, name: str = "") -> str:
    if isinstance(value, bool): return "Boolean"
    if isinstance(value, int): return "Int4"
    if isinstance(value, float): return "Float8"
    if isinstance(value, str): return "String"
    low = name.lower()
    if any(k in low for k in ("status", "alarm", "fault", "failure", "trip", "on_off", "on off", "comm")):
        return "Int4"
    return "Float8"


def normalized_unit(name: str, raw: str | None) -> tuple[str | None, str | None]:
    if raw:
        # preserve raw unless it is an explicit known ambiguity handled by review.
        return raw, None
    n = name.lower()
    patterns = [
        (("temperature", "temp"), "°C"), (("humidity",), "%RH"), (("pressure",), "kPa"),
        (("frequency", "hz"), "Hz"), (("current",), "A"), (("voltage",), "V"),
        (("active power", "input power", "output power", "ptot"), "kW"),
        (("reactive power", "qtot"), "kVAR"), (("apparent power", "stot"), "kVA"),
        (("energy", "wh_im"), "kWh"), (("flow",), "L/s"), (("ping",), "ms"),
        (("speed", "load", "position", "utilization", "soc", "capacity"), "%"),
    ]
    for keys, unit in patterns:
        if any(k in n for k in keys): return unit, "safe-name-normalization"
    return None, None


def classify(path: str, member: str, data_type: str, value: Any) -> tuple[str, str, bool]:
    root = path.split("/", 1)[0]
    scope = "TEST" if root in TEST_ROOTS else "SUPPORT" if root in SUPPORT_ROOTS else "PRODUCTION"
    n = member.lower()
    if scope != "PRODUCTION":
        return "TEST_SUPPORT" if scope == "TEST" else "SUPPORT_CONTROL", scope, False
    if any(k in n for k in ("command", "setpoint", "set point", "control")):
        cls = "CONTROL_COMMAND"
    elif any(k in n for k in ("feedback", "actual position")):
        cls = "CONTROL_FEEDBACK"
    elif any(k in n for k in ("alarm", "fault", "failure", "trip", "trouble")):
        cls = "FAULT_STATE"
    elif any(k in n for k in ("energy", "wh_im", "accumulated")):
        cls = "ENERGY_INTEGRAL"
    elif root in {"Network Topology", "Network Switches"} or any(k in n for k in ("ping", "uptime", "link status", "admin status", "error count", "utilization")):
        cls = "NETWORK_STATE"
    elif root in {"Environment Monitoring", "Temperature and Humidity"}:
        cls = "ENVIRONMENT"
    elif root == "Dashboard":
        cls = "PRESENTATION_DERIVED"
    elif any(k in n for k in ("power", "voltage", "current", "frequency", "pf", "thd", "ptot", "qtot", "stot")):
        cls = "ELECTRICAL_DERIVED"
    elif data_type == "String" or any(k in n for k in ("description", "rack id", "name", "location", "type")):
        cls = "STATIC_METADATA"
    elif any(k in n for k in ("status", "on_off", "on off", "mode", "running", "run")):
        cls = "EQUIPMENT_STATE"
    else:
        cls = "PHYSICAL_DERIVED"
    return cls, scope, cls not in {"STATIC_METADATA", "SUPPORT_CONTROL", "TEST_SUPPORT"}


@dataclass
class ParsedGraphene:
    udt_types: list[dict]
    instances: list[dict]
    folders: list[dict]
    points: list[dict]
    reviews: list[dict]

    @property
    def counts(self) -> dict[str, int]:
        return {
            "udtTypes": len(self.udt_types), "udtInstances": len(self.instances),
            "folders": len(self.folders), "atomicTags": len(self.points),
        }


def parse_exports(udt_file: Path, instance_file: Path) -> ParsedGraphene:
    udt_data = json.loads(udt_file.read_text(encoding="utf-8"))
    inst_data = json.loads(instance_file.read_text(encoding="utf-8"))
    udt_types, def_index = [], {}
    type_nodes: dict[str, dict] = {}
    leaf_nodes: dict[str, dict] = {}
    for node, path in _walk(udt_data.get("tags", [])):
        if node.get("tagType") == "UdtType":
            udt_types.append({"typeId": path, "name": node.get("name"), "parameters": node.get("parameters", {})})
            type_nodes[path] = node
            leaf_nodes.setdefault(node.get("name", path), node)

    def resolve_type(type_id: str | None) -> dict | None:
        if not type_id: return None
        return type_nodes.get(type_id) or leaf_nodes.get(type_id)

    def flatten_type(type_id: str, stack: tuple[str, ...] = ()) -> dict[str, dict]:
        if type_id in def_index: return def_index[type_id]
        if type_id in stack: return {}
        node=resolve_type(type_id)
        if not node: return {}
        atoms: dict[str,dict] = {}
        def scan(children, rel=""):
            for child in children or []:
                cp=f"{rel}/{child.get('name','')}".strip('/')
                if child.get('tagType')=='AtomicTag':
                    atoms[cp]=child
                elif child.get('tagType')=='UdtInstance' and child.get('typeId'):
                    referenced=flatten_type(child['typeId'], stack+(type_id,))
                    for rp,meta in referenced.items(): atoms[f"{cp}/{rp}".strip('/')]=meta
                    scan(child.get('tags',[]),cp)
                else:
                    scan(child.get('tags',[]),cp)
        scan(node.get('tags',[]))
        def_index[type_id]=atoms
        return atoms

    for row in udt_types:
        atoms=flatten_type(row['typeId'])
        def_index.setdefault(row['name'], atoms)

    folders, instances, points, reviews = [], [], [], []

    def walk_instances(nodes, parent="", root_instance=None, root_type=None, rel=""):
        for node in nodes or []:
            name = node.get("name", "")
            path = f"{parent}/{name}".strip("/")
            t = node.get("tagType")
            if t == "Folder":
                folders.append({"path": path, "name": name})
                walk_instances(node.get("tags", []), path, root_instance, root_type, rel)
                continue
            if t == "UdtInstance":
                current_root = root_instance or path
                current_type = root_type or node.get("typeId")
                current_rel = "" if root_instance is None else f"{rel}/{name}".strip("/")
                instances.append({
                    "path": path, "name": name, "typeId": node.get("typeId") or current_type,
                    "declaredTypeId": node.get("typeId"), "rootInstancePath": current_root,
                    "parameters": deepcopy(node.get("parameters", {})),
                })
                walk_instances(node.get("tags", []), path, current_root, current_type, current_rel)
                continue
            if t == "AtomicTag":
                member_path = f"{rel}/{name}".strip("/") if root_instance else name
                definition = (def_index.get(root_type or "", {}) or {}).get(member_path, {})
                raw_type = node.get("dataType") or definition.get("dataType")
                dtype = raw_type or infer_type(node.get("value"), name)
                raw_unit = node.get("engUnit") or definition.get("engUnit")
                unit, unit_reason = normalized_unit(name, raw_unit)
                if raw_type is None:
                    reviews.append({"exportPath": path, "field": "dataType", "sourceValue": None, "normalizedValue": dtype, "confidence": "medium", "reason": "inferred from source value/member semantics"})
                if unit_reason:
                    reviews.append({"exportPath": path, "field": "engUnit", "sourceValue": None, "normalizedValue": unit, "confidence": "high", "reason": unit_reason})
                # Do not normalize known conflicting explicit unit; flag it.
                if "input power" in name.lower() and raw_unit == "V":
                    reviews.append({"exportPath": path, "field": "engUnit", "sourceValue": raw_unit, "normalizedValue": raw_unit, "confidence": "review-required", "reason": "explicit export unit conflicts with power semantics; preserved"})
                source_class, scope, ai_visible = classify(path, name, dtype, node.get("value"))
                alarms = deepcopy(node.get("alarms") or definition.get("alarms") or [])
                history_intent = bool(node.get("historyEnabled", definition.get("historyEnabled", False)))
                type_id = root_type
                points.append({
                    "exportPath": path, "folderPath": path.rsplit("/", 1)[0] if "/" in path else "",
                    "instanceName": root_instance.rsplit("/",1)[-1] if root_instance else None,
                    "instancePath": root_instance, "typeId": type_id, "memberName": name,
                    "memberPath": member_path, "dataType": dtype, "engUnit": unit,
                    "historyIntent": history_intent, "alarmDefinitions": alarms,
                    "sourceClass": source_class, "simulationOwner": "DomainModel" if source_class in RUNTIME_CLASSES else "SchemaProjection",
                    "domainSignal": stable_key(path), "signalKey": stable_key(path),
                    "origin": "graphene-normalized" if (raw_type is None or unit_reason) else "graphene-export",
                    "scope": scope, "aiVisible": ai_visible, "runtimeRequired": source_class in RUNTIME_CLASSES,
                    "sourceValue": deepcopy(node.get("value")),
                    "sourceMetadata": {k: deepcopy(v) for k, v in node.items() if k not in {"tags", "value"}},
                })
                walk_instances(node.get("tags", []), path, root_instance, root_type, member_path)
            else:
                walk_instances(node.get("tags", []), path, root_instance, root_type, rel)

    walk_instances(inst_data.get("tags", []))
    return ParsedGraphene(udt_types, instances, folders, points, reviews)


def inventory_counts(path: Path) -> Counter:
    data = json.loads(path.read_text(encoding="utf-8")); c = Counter()
    for n, _ in _walk(data.get("tags", [])): c[n.get("tagType", "Other")] += 1
    return c
