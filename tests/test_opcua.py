import asyncio
import json
from datetime import datetime, timezone

from asyncua import Server, ua

from graphene_demo_twin.adapters.opcua import (
    NAMESPACE,
    _build_address_space,
    _encode_point_value,
)
from graphene_demo_twin.runtime.engine import RuntimeEngine


def test_all_projected_points_have_stable_opc_encoding():
    points = RuntimeEngine().snapshot()["points"]

    for point in points.values():
        value, variant_type = _encode_point_value(point, ua)
        # Construction performs asyncua's type validation without needing a network server.
        ua.Variant(value, variant_type)


def test_full_address_space_accepts_literal_colon_browse_names():
    async def exercise():
        runtime = RuntimeEngine()
        server = Server()
        await server.init()
        try:
            idx, variables = await _build_address_space(server, runtime, ua)
            by_path = {path: variable for path, variable, _ in variables}

            assert len(variables) == len(runtime.snapshot()["points"])

            path = "Genset/Genset 3/AC Voltage: L2-N"
            variable = by_path[path]
            assert variable.nodeid == ua.NodeId(f"point:{path}", idx)
            assert await variable.read_browse_name() == ua.QualifiedName("AC Voltage: L2-N", idx)

            document = by_path["Device Card Abbreviation"]
            document_value = await document.read_value()
            assert isinstance(document_value, str)
            assert json.loads(document_value)["Avg Vin"] == "Average Input Voltage"

            moving_until = by_path["Lift Monitoring System/Lift 1/Moving Until"]
            moving_until_value = await moving_until.read_value()
            assert isinstance(moving_until_value, datetime)
            assert moving_until_value.tzinfo is not None
            assert moving_until_value.astimezone(timezone.utc) == moving_until_value

            access = await variable.get_access_level()
            assert ua.AccessLevel.CurrentWrite not in access
            assert await server.get_namespace_index(NAMESPACE) == idx
        finally:
            await server.stop()

    asyncio.run(exercise())
