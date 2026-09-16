from __future__ import annotations
from pathlib import Path
from typing import Any
import json

REQUIRED: dict[str, list[tuple[str, str, str | None, str]]] = {
    "CRAC": [
        ("CHW Valve Command", "Float8", "%", "AI-diagnostic-observability"),
        ("CHW Valve Feedback", "Float8", "%", "AI-diagnostic-observability"),
        ("CHW Flow", "Float8", "L/s", "root-cause-evidence"),
        ("CHW Supply Temperature", "Float8", "°C", "physical-consistency"),
        ("CHW Return Temperature", "Float8", "°C", "physical-consistency"),
        ("Fan Electrical Power", "Float8", "kW", "AI-diagnostic-observability"),
    ],
    "Chiller": [
        ("Load", "Float8", "%", "AI-diagnostic-observability"), ("Cooling Output", "Float8", "kW", "physical-consistency"),
        ("COP", "Float8", None, "root-cause-evidence"), ("kW per RT", "Float8", "kW/RT", "root-cause-evidence"),
        ("CHW Supply Temperature", "Float8", "°C", "root-cause-evidence"), ("CHW Return Temperature", "Float8", "°C", "root-cause-evidence"),
        ("CHW Flow", "Float8", "L/s", "root-cause-evidence"), ("CW Supply Temperature", "Float8", "°C", "root-cause-evidence"),
        ("CW Return Temperature", "Float8", "°C", "root-cause-evidence"), ("CW Flow", "Float8", "L/s", "root-cause-evidence"),
        ("CW Approach", "Float8", "K", "root-cause-evidence"),
    ],
    "Chiller Pump": [
        ("Current", "Float8", "A", "AI-diagnostic-observability"), ("Frequency", "Float8", "Hz", "AI-diagnostic-observability"),
        ("Speed Command", "Float8", "%", "AI-diagnostic-observability"), ("Speed Feedback", "Float8", "%", "AI-diagnostic-observability"),
        ("Flow", "Float8", "L/s", "root-cause-evidence"), ("Suction Pressure", "Float8", "kPa", "root-cause-evidence"),
        ("Discharge Pressure", "Float8", "kPa", "root-cause-evidence"), ("Differential Pressure", "Float8", "kPa", "root-cause-evidence"),
        ("HasAlarm", "Boolean", None, "AI-diagnostic-observability"), ("Run Hours", "Float8", "h", "AI-diagnostic-observability"),
    ],
    "Chiller Valve": [
        ("Command", "Float8", "%", "AI-diagnostic-observability"), ("Feedback", "Float8", "%", "AI-diagnostic-observability"),
        ("Position", "Float8", "%", "AI-diagnostic-observability"), ("Fault", "Boolean", None, "root-cause-evidence"),
    ],
    "Cooling Tower": [
        ("CWR Temperature", "Float8", "°C", "root-cause-evidence"), ("CWS Temperature", "Float8", "°C", "root-cause-evidence"),
        ("Approach", "Float8", "K", "root-cause-evidence"), ("Fan Speed Command", "Float8", "%", "AI-diagnostic-observability"),
        ("Fan Speed Feedback", "Float8", "%", "AI-diagnostic-observability"), ("Basin Level", "Float8", "%", "AI-diagnostic-observability"),
        ("Run Hours", "Float8", "h", "AI-diagnostic-observability"),
    ],
    "PAHU": [
        ("Static Pressure", "Float8", "Pa", "root-cause-evidence"), ("Static Pressure Setpoint", "Float8", "Pa", "root-cause-evidence"),
        ("Fan Speed Command", "Float8", "%", "AI-diagnostic-observability"), ("Fan Speed Feedback", "Float8", "%", "AI-diagnostic-observability"),
        ("CHW Valve Command", "Float8", "%", "AI-diagnostic-observability"), ("CHW Valve Feedback", "Float8", "%", "AI-diagnostic-observability"),
        ("CHW Flow", "Float8", "L/s", "root-cause-evidence"),
    ],
    "FWU": [("Fan Speed Command", "Float8", "%", "AI-diagnostic-observability"), ("Fan Speed Feedback", "Float8", "%", "AI-diagnostic-observability"), ("CHW Valve Command", "Float8", "%", "AI-diagnostic-observability"), ("CHW Valve Feedback", "Float8", "%", "AI-diagnostic-observability")],
    "FCU": [("Fan Speed Command", "Float8", "%", "AI-diagnostic-observability"), ("Fan Speed Feedback", "Float8", "%", "AI-diagnostic-observability"), ("CHW Valve Command", "Float8", "%", "AI-diagnostic-observability"), ("CHW Valve Feedback", "Float8", "%", "AI-diagnostic-observability")],
    "UPS": [
        ("Load %", "Float8", "%", "AI-diagnostic-observability"), ("Output Power", "Float8", "kW", "AI-diagnostic-observability"),
        ("Inverter Status", "Boolean", None, "AI-diagnostic-observability"), ("Bypass Status", "Boolean", None, "AI-diagnostic-observability"),
        ("Battery SOC", "Float8", "%", "AI-diagnostic-observability"), ("Battery Voltage", "Float8", "V", "AI-diagnostic-observability"),
        ("Battery Current", "Float8", "A", "AI-diagnostic-observability"), ("Battery Runtime Remaining", "Float8", "min", "AI-diagnostic-observability"),
        ("Battery Charging Failure", "Boolean", None, "root-cause-evidence"), ("UPS Common Alarm", "Boolean", None, "root-cause-evidence"),
    ],
    "Genset": [
        ("Active Power", "Float8", "kW", "AI-diagnostic-observability"), ("Reactive Power", "Float8", "kVAR", "AI-diagnostic-observability"),
        ("Apparent Power", "Float8", "kVA", "AI-diagnostic-observability"), ("Load %", "Float8", "%", "AI-diagnostic-observability"),
        ("Current", "Float8", "A", "AI-diagnostic-observability"), ("Energy", "Float8", "kWh", "AI-diagnostic-observability"),
        ("Breaker Status", "Boolean", None, "AI-diagnostic-observability"), ("Fuel Level", "Float8", "%", "AI-diagnostic-observability"),
    ],
    "CDU": [
        ("Supply Coolant Temperature", "Float8", "°C", "AI-diagnostic-observability"), ("Return Coolant Temperature", "Float8", "°C", "AI-diagnostic-observability"),
        ("Flow", "Float8", "L/s", "AI-diagnostic-observability"), ("Differential Pressure", "Float8", "kPa", "AI-diagnostic-observability"),
        ("Pump Run", "Boolean", None, "AI-diagnostic-observability"), ("Pump Speed", "Float8", "%", "AI-diagnostic-observability"),
        ("Pump Power", "Float8", "kW", "AI-diagnostic-observability"), ("IT Load", "Float8", "kW", "AI-diagnostic-observability"),
        ("Facility Load", "Float8", "kW", "AI-diagnostic-observability"), ("Heat Load", "Float8", "kW", "AI-diagnostic-observability"),
        ("Unit Running Status", "Boolean", None, "AI-diagnostic-observability"), ("Alarm Status", "Boolean", None, "AI-diagnostic-observability"),
    ],
}


def build_extensions(topology: dict[str, Any], coverage: dict[str, Any]) -> list[dict[str, Any]]:
    existing_by_instance: dict[str, set[str]] = {}
    existing_paths = {e["exportPath"] for e in coverage["entries"]}
    for entry in coverage["entries"]:
        if entry.get("instancePath"):
            existing_by_instance.setdefault(entry["instancePath"], set()).add(entry["memberName"])
    result: list[dict[str, Any]] = []
    for instance in topology["instances"]:
        specs = REQUIRED.get(instance["type_id"], [])
        for name, dtype, unit, reason in specs:
            if name in existing_by_instance.get(instance["export_path"], set()):
                continue
            export_path = f"{instance['export_path']}/{name}"
            if export_path in existing_paths:
                raise ValueError(f"Extension shadows exported point: {export_path}")
            signal = instance["export_path"].lower().replace("/", ".").replace(" ", "_") + "." + name.lower().replace(" ", "_")
            result.append({
                "exportPath": export_path, "folderPath": instance["export_path"], "instancePath": instance["export_path"],
                "instanceName": instance["name"], "typeId": instance["type_id"], "memberName": name,
                "dataType": dtype, "engUnit": unit, "historyIntent": True, "alarmDefinitions": [],
                "sourceClass": "PHYSICAL_DERIVED", "simulationOwner": instance["export_path"], "domainSignal": signal,
                "signalKey": signal, "origin": "twin-extension", "extensionReason": reason, "scope": "PRODUCTION",
                "aiVisible": True, "sourceMetadata": {},
            })
    return result


def write_extensions(output_dir: str | Path) -> list[dict[str, Any]]:
    output_dir = Path(output_dir)
    topology = json.loads((output_dir / "graphene-instance-topology.json").read_text(encoding="utf-8"))
    coverage = json.loads((output_dir / "graphene-coverage-manifest.json").read_text(encoding="utf-8"))
    extensions = build_extensions(topology, coverage)
    (output_dir / "diagnostic-extensions.json").write_text(json.dumps({"entries": extensions}, indent=2, ensure_ascii=False), encoding="utf-8")
    return extensions
