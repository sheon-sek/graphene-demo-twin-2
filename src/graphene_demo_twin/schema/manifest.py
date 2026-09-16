from __future__ import annotations

from dataclasses import asdict
from hashlib import sha256
from pathlib import Path
from typing import Any
import json
import re

from .parser import AtomicPoint, UdtTypeInfo, count_nodes, load_json, parse_instances, parse_udt_types

RUNTIME_CLASSES = {
    "PHYSICAL_SOURCE", "PHYSICAL_DERIVED", "CONTROL_COMMAND", "CONTROL_FEEDBACK",
    "EQUIPMENT_STATE", "FAULT_STATE", "ELECTRICAL_DERIVED", "ENERGY_INTEGRAL",
    "ENVIRONMENT", "NETWORK_STATE", "AGGREGATE", "PRESENTATION_DERIVED",
}

SUPPORT_ROOTS = {"DemoRack", "MQTT Tags", "PredictionCache"}
TEST_ROOTS = {"Testing"}


def _definition_index(types: list[UdtTypeInfo]) -> dict[tuple[str, str], dict[str, Any]]:
    index: dict[tuple[str, str], dict[str, Any]] = {}
    for udt in types:
        aliases = {udt.path, udt.name, udt.path.removeprefix("Production/")}
        for member in udt.members:
            member_name = str(member.get("name", ""))
            member_path = str(member.get("memberPath", member_name))
            for alias in aliases:
                index[(alias, member_name)] = member
                index[(alias, member_path)] = member
    return index


def _root(path: str) -> str:
    return path.split("/", 1)[0]


def _scope(path: str) -> str:
    root = _root(path)
    if root in TEST_ROOTS:
        return "TEST"
    if root in SUPPORT_ROOTS:
        return "SUPPORT"
    return "PRODUCTION"


def _classify(point: AtomicPoint) -> str:
    path = point.export_path.lower()
    name = point.member_name.lower()
    root = _root(point.export_path)
    if root in TEST_ROOTS:
        return "TEST_SUPPORT"
    if root in SUPPORT_ROOTS:
        return "SUPPORT_CONTROL"
    if root == "Dashboard":
        return "PRESENTATION_DERIVED"
    if root in {"Network Switches", "Network Topology"}:
        return "NETWORK_STATE"
    if root in {"Environment Monitoring", "Temperature and Humidity"}:
        return "ENVIRONMENT"
    if any(token in name for token in ("alarm", "fault", "trip", "failure", "fail to", "leak status")):
        return "FAULT_STATE"
    if any(token in name for token in ("command", "setpoint", "set point", "control")):
        return "CONTROL_COMMAND"
    if any(token in name for token in ("feedback", "run status", "running status")):
        return "CONTROL_FEEDBACK"
    if any(token in name for token in ("on_off", "on off", "status", "mode", "interlock")):
        return "EQUIPMENT_STATE"
    if any(token in name for token in ("accumulated energy", "energy", "wh_im", "kwh")):
        return "ENERGY_INTEGRAL"
    if root in {"Meter", "BCPM", "Breaker", "UPS", "IPS", "RCMS", "Genset"}:
        if re.match(r"^(p|q|s|pf|i|v|hz|thd)", name) or any(x in name for x in ("power", "current", "voltage", "frequency")):
            return "ELECTRICAL_DERIVED"
    if any(token in name for token in ("temperature", "humidity", "wet bulb", "dew point")):
        return "ENVIRONMENT" if "environment" in path or root == "Temperature and Humidity" else "PHYSICAL_DERIVED"
    if any(token in name for token in ("description", "rack id", "location", "name", "type")):
        source_value = point.source_metadata.get("value")
        if isinstance(source_value, str):
            return "STATIC_METADATA"
    return "PHYSICAL_DERIVED"


def _normalize_data_type(source: dict[str, Any], definition: dict[str, Any] | None, member_name: str) -> tuple[str, bool, str]:
    if source.get("dataType"):
        return str(source["dataType"]), False, "export-instance"
    if definition and definition.get("dataType"):
        return str(definition["dataType"]), True, "udt-definition"
    value = source.get("value", source.get("defaultValue"))
    if isinstance(value, bool):
        return "Boolean", True, "inferred-from-source-value"
    if isinstance(value, int):
        return "Int4", True, "inferred-from-source-value"
    if isinstance(value, float):
        return "Float8", True, "inferred-from-source-value"
    if isinstance(value, str):
        return "String", True, "inferred-from-source-value"
    lowered = member_name.lower()
    if any(token in lowered for token in ("status", "alarm", "fault", "trip", "on_off", "on off", "fail", "interlock")):
        return "Boolean", True, "semantic-conservative-default"
    if any(token in lowered for token in ("description", "rack id", "location", "name")):
        return "String", True, "semantic-conservative-default"
    return "Float8", True, "numeric-conservative-default"


def _normalize_unit(source: dict[str, Any], definition: dict[str, Any] | None, member_name: str) -> tuple[str | None, bool, str | None]:
    if source.get("engUnit") not in (None, ""):
        return str(source["engUnit"]), False, None
    if definition and definition.get("engUnit") not in (None, ""):
        return str(definition["engUnit"]), True, "udt-definition"
    n = member_name.lower()
    safe = [
        (("temperature", " temp"), "°C"), (("humidity",), "%"), (("frequency", "hz"), "Hz"),
        (("current",), "A"), (("voltage",), "V"), (("active power", "input power", "output power"), "kW"),
        (("reactive power",), "kVAR"), (("apparent power",), "kVA"), (("flow",), "L/s"),
        (("pressure",), "kPa"), (("energy", "wh_im"), "kWh"), (("position", "load %", "speed"), "%"),
    ]
    for tokens, unit in safe:
        if any(token in n for token in tokens):
            return unit, True, "semantic-normalization"
    return None, False, None


def _signal_key(point: AtomicPoint) -> str:
    owner = point.instance_path or point.folder_path or _root(point.export_path)
    slug = re.sub(r"[^a-z0-9]+", ".", owner.lower()).strip(".")
    member = re.sub(r"[^a-z0-9]+", "_", point.member_name.lower()).strip("_")
    return f"{slug}.{member}" if slug else member


def generate(reference_dir: str | Path, output_dir: str | Path) -> dict[str, Any]:
    reference_dir = Path(reference_dir)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    defs_doc = load_json(reference_dir / "real-graphene-demo-udt-definitions.json")
    inst_doc = load_json(reference_dir / "real-graphene-demo-tag-instances.json")
    types = parse_udt_types(defs_doc)
    folders, instances, points = parse_instances(inst_doc)
    defs = _definition_index(types)

    manifest: list[dict[str, Any]] = []
    review: list[dict[str, Any]] = []
    for point in points:
        definition = defs.get((point.type_id or "", point.member_name))
        data_type, dt_norm, dt_reason = _normalize_data_type(point.source_metadata, definition, point.member_name)
        unit, unit_norm, unit_reason = _normalize_unit(point.source_metadata, definition, point.member_name)
        source_class = _classify(point)
        scope = _scope(point.export_path)
        ai_visible = scope == "PRODUCTION" and source_class not in {"SUPPORT_CONTROL", "TEST_SUPPORT"}
        alarms = point.source_metadata.get("alarms")
        if alarms is None and definition:
            alarms = definition.get("alarms")
        history = point.source_metadata.get("historyEnabled")
        if history is None and definition:
            history = definition.get("historyEnabled", False)
        signal = _signal_key(point)
        entry = {
            "exportPath": point.export_path,
            "folderPath": point.folder_path,
            "instancePath": point.instance_path,
            "instanceName": point.instance_name,
            "typeId": point.type_id,
            "memberName": point.member_name,
            "dataType": data_type,
            "engUnit": unit,
            "historyIntent": bool(history),
            "alarmDefinitions": alarms or [],
            "sourceClass": source_class,
            "simulationOwner": point.instance_path or point.folder_path or _root(point.export_path),
            "domainSignal": signal,
            "signalKey": signal,
            "origin": "graphene-normalized" if (dt_norm or unit_norm) else "graphene-export",
            "scope": scope,
            "aiVisible": ai_visible,
            "sourceMetadata": point.source_metadata,
        }
        manifest.append(entry)
        if dt_norm:
            review.append({"exportPath": point.export_path, "field": "dataType", "sourceValue": point.source_metadata.get("dataType"), "normalizedValue": data_type, "confidence": "high" if dt_reason != "numeric-conservative-default" else "medium", "reason": dt_reason})
        if unit_norm:
            review.append({"exportPath": point.export_path, "field": "engUnit", "sourceValue": point.source_metadata.get("engUnit"), "normalizedValue": unit, "confidence": "medium", "reason": unit_reason})

    schema = {
        "source": "real-graphene-demo-udt-definitions.json",
        "counts": count_nodes(defs_doc),
        "udtTypes": [asdict(item) for item in types],
    }
    topology = {
        "source": "real-graphene-demo-tag-instances.json",
        "counts": count_nodes(inst_doc),
        "folders": folders,
        "instances": [asdict(item) | {"zoneId": infer_zone(item.export_path), "roomId": infer_room(item.export_path)} for item in instances],
        "relationships": build_relationships(instances),
    }
    coverage = {
        "authority": "real-graphene-demo-tag-instances.json",
        "exportAtomicTagCount": len(points),
        "manifestEntryCount": len(manifest),
        "unmappedCount": len({p.export_path for p in points} - {m["exportPath"] for m in manifest}),
        "entries": manifest,
        "sha256": sha256("\n".join(sorted(m["exportPath"] for m in manifest)).encode()).hexdigest(),
    }
    (output_dir / "graphene-schema.json").write_text(json.dumps(schema, indent=2, ensure_ascii=False), encoding="utf-8")
    (output_dir / "graphene-instance-topology.json").write_text(json.dumps(topology, indent=2, ensure_ascii=False), encoding="utf-8")
    (output_dir / "graphene-coverage-manifest.json").write_text(json.dumps(coverage, indent=2, ensure_ascii=False), encoding="utf-8")
    (output_dir / "normalization-review.json").write_text(json.dumps({"reviews": review}, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"schema": schema, "topology": topology, "coverage": coverage, "reviewCount": len(review)}


def infer_zone(path: str) -> str:
    p = path.lower()
    if "level 2" in p or "/l2" in p:
        return "Level-2"
    if "level 1" in p or "/l1_" in p:
        return "Level-1"
    if "roof" in p or "/r_" in p:
        return "Roof"
    if "ground" in p or "/g_" in p:
        return "Ground"
    return "Site"


def infer_room(path: str) -> str:
    p = path.lower()
    if any(k in p for k in ("crac", "pahu", "fwu", "fcu", "temperature", "environment")):
        return "Data-Hall"
    if any(k in p for k in ("chiller", "cooling tower", "buffer tank")):
        return "Cooling-Plant"
    if any(k in p for k in ("ups", "meter", "bcpm", "breaker", "genset", "diesel")):
        return "Electrical"
    if "network" in p:
        return "Network"
    if any(k in p for k in ("water", "leak")):
        return "Water-Utility"
    return "Facility"


def build_relationships(instances) -> list[dict[str, str]]:
    relationships: list[dict[str, str]] = []
    for item in instances:
        if item.folder_path:
            relationships.append({"type": "contains", "from": item.folder_path, "to": item.export_path})
        relationships.append({"type": "locatedIn", "from": item.export_path, "to": infer_zone(item.export_path)})
    # Conservative system-level causal links; exact engineering wiring remains configurable.
    groups: dict[str, list[str]] = {}
    for item in instances:
        groups.setdefault(item.type_id, []).append(item.export_path)
    for child_type, parent_type in [("CRAC", "Chiller"), ("PAHU", "Chiller"), ("FWU", "Chiller"), ("FCU", "Chiller"), ("BCPM", "Meter")]:
        parents = sorted(groups.get(parent_type, []))
        if not parents:
            continue
        for idx, child in enumerate(sorted(groups.get(child_type, []))):
            relationships.append({"type": "servedBy" if parent_type == "Chiller" else "meteredBy", "from": child, "to": parents[idx % len(parents)]})
    return relationships
