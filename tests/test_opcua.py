import asyncio
import json
import socket
from contextlib import suppress
from datetime import datetime, timezone

import pytest
from asyncua import Client, Server, ua

from graphene_demo_twin.adapters.opcua import (
    NAMESPACE,
    _build_address_space,
    _encode_point_value,
    serve,
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

        # asyncua 1.1.8 Server.stop() assumes start() created a binary server.
        # This test intentionally exercises address-space construction only, so do not call stop().

    asyncio.run(exercise())


def test_opc_server_client_smoke_is_read_only_and_preserves_source_timestamp():
    class TinyRuntime:
        def __init__(self):
            self.timestamp = datetime(2026, 8, 28, 6, 0, tzinfo=timezone.utc)
            self.points = {
                "Genset/Genset 1/AC Voltage: L2-N": {
                    "exportPath": "Genset/Genset 1/AC Voltage: L2-N",
                    "dataType": "Float8",
                    "value": 230.5,
                },
                "Device Card Abbreviation": {
                    "exportPath": "Device Card Abbreviation",
                    "dataType": "Document",
                    "value": {"Avg Vin": "Average Input Voltage"},
                },
            }

        def snapshot(self):
            return {"points": self.points}

        def now(self):
            return self.timestamp

    async def exercise():
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]

        endpoint = f"opc.tcp://127.0.0.1:{port}/graphene/twin"
        runtime = TinyRuntime()
        task = asyncio.create_task(serve(runtime, endpoint))
        client = Client(endpoint)
        connected = False

        try:
            for _ in range(100):
                try:
                    await client.connect()
                    connected = True
                    break
                except Exception:
                    await asyncio.sleep(0.05)
            assert connected, "OPC UA client could not connect to the test server"

            idx = await client.get_namespace_index(NAMESPACE)
            path = "Genset/Genset 1/AC Voltage: L2-N"
            node = client.get_node(ua.NodeId(f"point:{path}", idx))

            assert await node.read_browse_name() == ua.QualifiedName("AC Voltage: L2-N", idx)
            assert await node.read_value() == 230.5

            data_value = await node.read_data_value()
            assert data_value.SourceTimestamp is not None
            assert data_value.SourceTimestamp.astimezone(timezone.utc) == runtime.timestamp

            access = await node.get_access_level()
            assert ua.AccessLevel.CurrentWrite not in access
            with pytest.raises(ua.UaStatusCodeError):
                await node.write_value(231.0)
        finally:
            if connected:
                await client.disconnect()
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    asyncio.run(exercise())
