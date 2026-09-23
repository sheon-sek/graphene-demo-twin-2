from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any

from graphene_demo_twin.runtime.engine import RuntimeEngine

NAMESPACE = "urn:eetarp:graphene:demo:twin"

# Graphene/Ignition data types mapped to stable OPC UA built-in VariantTypes.
# Document/DataSet do not have a native scalar representation in this surface, so
# they are exposed as deterministic JSON strings instead of Python container objects.
_GRAPHENE_VARIANT_TYPES = {
    "Boolean": "Boolean",
    "Float4": "Float",
    "Float8": "Double",
    "Int4": "Int32",
    "Int8": "Int64",
    "String": "String",
    "DateTime": "DateTime",
    "DataSet": "String",
    "Document": "String",
}


def _utc_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        # Ignition DateTime values in the Graphene export/projected surface are epoch milliseconds.
        result = datetime.fromtimestamp(value / 1000.0, tz=timezone.utc)
    elif isinstance(value, str):
        text = value.strip()
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        result = datetime.fromisoformat(text)
    else:
        raise TypeError(f"Unsupported DateTime value: {value!r}")

    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _encode_point_value(point: dict[str, Any], ua: Any) -> tuple[Any, Any]:
    data_type = point.get("dataType")
    value = point.get("value")

    if data_type == "DateTime":
        value = _utc_datetime(value)
    elif data_type in {"Document", "DataSet"} and not isinstance(value, str):
        value = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    variant_name = _GRAPHENE_VARIANT_TYPES.get(data_type)
    if variant_name is not None:
        return value, getattr(ua.VariantType, variant_name)

    # Keep extensions usable while refusing container values that asyncua cannot infer safely.
    if isinstance(value, bool):
        return value, ua.VariantType.Boolean
    if isinstance(value, int):
        return value, ua.VariantType.Int64
    if isinstance(value, float):
        return value, ua.VariantType.Double
    if isinstance(value, str):
        return value, ua.VariantType.String
    raise TypeError(
        f"Unsupported OPC UA value for {point.get('exportPath', '<unknown>')}: "
        f"dataType={data_type!r}, pythonType={type(value).__name__}"
    )


async def _build_address_space(server: Any, runtime: RuntimeEngine, ua: Any):
    idx = await server.register_namespace(NAMESPACE)
    folders = {"": server.nodes.objects}
    variables = []
    snapshot = runtime.snapshot()["points"]

    for path, point in snapshot.items():
        parts = path.split("/")
        parent = ""
        for segment in parts[:-1]:
            key = f"{parent}/{segment}".strip("/")
            if key not in folders:
                # Passing a namespace index as the first argument makes asyncua construct
                # the QualifiedName directly, so folder labels remain literal.
                folders[key] = await folders[parent].add_folder(idx, segment)
            parent = key

        value, variant_type = _encode_point_value(point, ua)
        nodeid = ua.NodeId(f"point:{path}", idx)
        # IMPORTANT: with an explicit NodeId, asyncua parses a *string* BrowseName using
        # QualifiedName.from_string(). A literal ':' would therefore be interpreted as a
        # namespace separator (e.g. "AC Voltage: L2-N"). Always pass QualifiedName directly.
        browse_name = ua.QualifiedName(parts[-1], idx)
        variable = await folders[parent].add_variable(
            nodeid,
            browse_name,
            value,
            varianttype=variant_type,
        )
        variables.append((path, variable, variant_type))

    return idx, variables


async def serve(
    runtime: RuntimeEngine,
    endpoint: str = "opc.tcp://127.0.0.1:4840/graphene/twin",
):
    try:
        from asyncua import Server, ua
    except ImportError as exc:
        raise RuntimeError("Install project dependencies with: uv sync") from exc

    server = Server()
    await server.init()
    server.set_endpoint(endpoint)
    _, variables = await _build_address_space(server, runtime, ua)
    await server.start()

    try:
        while True:
            # Offload the CPU-bound snapshot to a worker thread: called synchronously
            # here it would block this event loop for seconds, leaving the OPC UA
            # server unable to service client reads and keepalives mid-cycle.
            points = (await asyncio.to_thread(runtime.snapshot))["points"]
            source_timestamp = runtime.now()
            for path, variable, variant_type in variables:
                value, current_variant_type = _encode_point_value(points[path], ua)
                if current_variant_type != variant_type:
                    raise TypeError(
                        f"OPC UA type changed for {path}: "
                        f"{variant_type.name} -> {current_variant_type.name}"
                    )
                data_value = ua.DataValue(
                    ua.Variant(value, variant_type),
                    SourceTimestamp=source_timestamp,
                )
                await variable.write_value(data_value)
            await asyncio.sleep(1)
    finally:
        await server.stop()
