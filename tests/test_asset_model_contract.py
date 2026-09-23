"""The Asset Model is a frozen contract (ADR-0001).

These tests fail if the parsed path set, per-point data types or UDT membership drift from
`reference/graphene/*.json`.
"""

import json
import shutil
from collections import Counter
from pathlib import Path

from graphene_demo_twin.asset_model import REFERENCE_DIR, load_asset_model

CONTRACT_SHA256 = "93f2fec4832414be9dc48bae82d975201bd577452c41fd534a8a60673e7afbce"


def _raw_export_counts() -> tuple[set[str], Counter[str]]:
    """Walk the raw instance export without the loader, as an independent oracle."""
    export = json.loads((REFERENCE_DIR / "real-graphene-demo-tag-instances.json").read_text())
    atomic_paths: set[str] = set()
    tag_types: Counter[str] = Counter()

    def walk(node: dict, prefix: str) -> None:
        for child in node.get("tags", []):
            path = f"{prefix}/{child['name']}" if prefix else child["name"]
            tag_types[child["tagType"]] += 1
            if child["tagType"] == "AtomicTag":
                atomic_paths.add(path)
            walk(child, path)

    walk(export, "")
    return atomic_paths, tag_types


def test_counts_match_the_export(asset_model):
    assert len(asset_model.udt_instances) == 831
    assert len(asset_model.assets) == 639
    assert len(asset_model.points) == 8741
    assert len(asset_model.udt_types) == 47


def test_point_paths_are_exactly_the_exported_atomic_tags(asset_model):
    atomic_paths, tag_types = _raw_export_counts()
    assert tag_types["AtomicTag"] == 8741
    assert tag_types["UdtInstance"] == 831
    assert set(asset_model.points) == atomic_paths


def test_contract_checksum_is_pinned(asset_model):
    assert asset_model.contract_checksum() == CONTRACT_SHA256


def test_every_udt_point_is_a_member_of_its_type(asset_model):
    for point in asset_model.points.values():
        if point.udt_instance is None:
            assert point.type_id is None and point.member is None and point.asset is None
            continue
        instance = asset_model.udt_instances[point.udt_instance]
        assert point.type_id == instance.type_id
        asset = asset_model.assets[point.asset]
        assert point.member in asset_model.udt_types[asset.type_id].members


def test_every_type_member_is_present_on_every_asset(asset_model):
    for asset in asset_model.assets.values():
        members = {p.member for p in asset_model.points_of(asset.path)}
        assert members == set(asset_model.udt_types[asset.type_id].members), asset.path


def test_data_types_resolve_through_overrides_nesting_and_inheritance(asset_model):
    # Declared on the UDT member.
    assert asset_model.point("Meter/Meter2/Wh_Im").data_type == "Float4"
    # Overridden to Float8 on the GPQM144 Pro definition, not on the GPM96 one.
    assert asset_model.point("DemoRack/GPQM144 Pro/Wh_Im").data_type == "Float8"
    # Member of a nested UDT instance (Network Switch Port) inside a Network Switch.
    port_speed = asset_model.point("Network Switches/MAIN CORE SWITCH A/Ports/Port 01/Speed")
    assert (port_speed.data_type, port_speed.type_id) == ("Int8", "Network Switch Port")
    # Inherited from the Dashboard type by CDU.
    assert asset_model.point("TIW/CDU-01/PUE").type_id == "CDU"
    assert asset_model.point("TIW/CDU-01/PUE").data_type == "Float8"
    # No dataType anywhere in the chain: Ignition's default.
    assert asset_model.point("Line/Backup/Line1").data_type == "Int4"
    assert asset_model.point("Chiller/R_C1/On_Off").data_type == "Int4"


def test_value_source_resolves_with_ignition_default(asset_model):
    assert asset_model.point("Meter/Meter2/V1").value_source == "opc"
    assert asset_model.point("Meter/Meter2/HasAlarm").value_source == "expr"
    assert asset_model.point("MQTT Tags/PLC 1/Example Tag").value_source == "memory"


def test_contract_checksum_detects_drift(tmp_path: Path, asset_model):
    copy = tmp_path / "graphene"
    shutil.copytree(REFERENCE_DIR, copy)
    definitions_path = copy / "real-graphene-demo-udt-definitions.json"
    definitions = json.loads(definitions_path.read_text())
    gpm96 = next(
        t
        for folder in definitions["tags"]
        if folder["name"] == "Production"
        for t in folder["tags"]
        if t["name"] == "GPM96"
    )
    member = next(m for m in gpm96["tags"] if m["name"] == "Hz")
    member["dataType"] = "Float8"
    definitions_path.write_text(json.dumps(definitions))

    drifted = load_asset_model(copy)
    assert drifted.point("DemoRack/GPM96/Hz").data_type == "Float8"
    assert drifted.contract_checksum() != asset_model.contract_checksum()
