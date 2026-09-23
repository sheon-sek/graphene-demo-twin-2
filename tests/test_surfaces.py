"""OPC UA, REST and SSE served together over real sockets from one twin (the Coherent World)."""

import asyncio
import json
import socket
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import httpx
import pytest
from asyncua import Client, ua

from graphene_demo_twin.serve import Services
from graphene_demo_twin.surfaces.api import iso
from graphene_demo_twin.surfaces.opcua import NAMESPACE, point_node_id
from graphene_demo_twin.twin import Twin

START = 1_790_000_000
HOT_AISLE_DH03 = "Temperature and Humidity/Datahall 3/Sensor 17/Temp"
SAMPLE = [
    HOT_AISLE_DH03,
    "BCPM/3L1/Active Power",
    "Dashboard/Total IT Load",
    "Dashboard/Energy/Floors/Level 1/Data Halls/DH03/IT Energy",
    "Genset/Genset 1/AC Voltage: L2-N",
    "Network Switches/MAIN CORE SWITCH A/Ports/Port 07/Speed",
    "Chiller System Control/Chillers/CH-004/Status",
    "BCPM/2L1/Rack ID",
    "Lift Monitoring System/Lift 1/Moving Until",
    "Device Card Abbreviation",
    "CRAC/L1_CRAC3/Supply Air Temperature",
    "CRAC/L1_CRAC3/Loss of Signal Alarm",
]
"""Physics, Plant View and fallback points across data types and path shapes."""
TRIP = {"target": "CRAC/L1_CRAC3", "fault": "crac.compressor_trip"}
COMM_LOSS = {"target": "CRAC/L1_CRAC3", "fault": "crac.comm_loss"}


class FakeClock:
    def __init__(self, now: float = START + 0.25) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@asynccontextmanager
async def _stack(asset_model, plant_design, tmp_path):
    clock = FakeClock()
    twin = Twin(asset_model, plant_design, seed=7, clock=clock)
    services = Services(
        twin, host="127.0.0.1", http_port=0, opc_port=_free_port(), console_dir=tmp_path
    )
    await services.start()
    opc = Client(services.opc.endpoint)
    http = httpx.AsyncClient(base_url=f"http://127.0.0.1:{services.http_port}", timeout=10)
    await opc.connect()
    try:
        yield twin, clock, services, opc, http
    finally:
        await opc.disconnect()
        await http.aclose()
        await services.stop()


async def _until(condition, timeout: float = 5.0) -> None:
    async with asyncio.timeout(timeout):
        while not condition():
            await asyncio.sleep(0.01)


def _opc_value(path: str, value, data_type: str):
    """The OPC UA form of a projected value."""
    if data_type == "DateTime":
        return datetime.fromtimestamp(value / 1000, UTC)
    return value


def test_opc_ua_browses_the_export_reads_sim_time_and_rejects_writes(
    asset_model, plant_design, tmp_path
):
    async def exercise():
        async with _stack(asset_model, plant_design, tmp_path) as stack:
            twin, clock, services, opc, _ = stack
            clock.now += 42
            frame = twin.tick()
            await _until(lambda: services.opc.published_seq == frame.seq)

            idx = await opc.get_namespace_index(NAMESPACE)
            assert len(services.opc.variables) == len(asset_model.points) == 8741

            for path in SAMPLE:
                # Browse down from Objects by the export path's segments.
                node = opc.nodes.objects
                for segment in path.split("/"):
                    node = await node.get_child(ua.QualifiedName(segment, idx))
                assert node.nodeid == point_node_id(path, idx)
                assert node.nodeid.Identifier == f"point:{path}"

                data_value = await node.read_data_value()
                point = asset_model.point(path)
                expected = _opc_value(path, frame.projection.values[path], point.data_type)
                if isinstance(expected, datetime):
                    assert data_value.Value.Value.astimezone(UTC) == expected, path
                else:
                    assert data_value.Value.Value == expected, path
                assert data_value.StatusCode.is_good()
                assert data_value.SourceTimestamp.astimezone(UTC) == datetime.fromtimestamp(
                    START + 42, UTC
                )

                with pytest.raises(ua.UaStatusCodeError):
                    await node.write_value(data_value.Value)
                assert ua.AccessLevel.CurrentWrite not in await node.get_access_level()

            # Variables carry the OPC UA type of their Ignition data type.
            float4 = opc.get_node(point_node_id(HOT_AISLE_DH03, idx))
            assert await float4.read_data_type_as_variant_type() == ua.VariantType.Float

    asyncio.run(exercise())


def test_every_surface_observes_the_same_live_world_step(asset_model, plant_design, tmp_path):
    async def exercise():
        async with _stack(asset_model, plant_design, tmp_path) as stack:
            twin, clock, services, opc, http = stack
            idx = await opc.get_namespace_index(NAMESPACE)
            nodes = {p: opc.get_node(point_node_id(p, idx)) for p in SAMPLE}

            async with http.stream("GET", "/api/stream") as stream:
                events = _sse_events(stream)
                snapshot = await anext(events)
                assert snapshot["event"] == "snapshot"
                assert snapshot["data"]["time"] == START
                assert len(snapshot["data"]["points"]) == 8741
                assert snapshot["data"]["state"]["DH03"] == twin.frame.state.assets["DH03"]
                seen = {p: v["value"] for p, v in snapshot["data"]["points"].items()}

                assert snapshot["data"]["faults"] == []
                for logged_count, fault in enumerate((TRIP, COMM_LOSS), 1):
                    assert (await http.post("/api/faults", json=fault)).status_code == 201
                    # Logging the event publishes the same step with the longer Event Log.
                    logged = await anext(events)
                    assert logged["data"]["time"] == START
                    assert logged["data"]["events"] == logged_count
                    assert logged["data"]["points"] == {} and logged["data"]["state"] == {}
                assert (await http.get("/api/events")).json()["time"] == START

                shown = START
                for elapsed in (1, 2, 30):
                    clock.now = START + 0.25 + elapsed
                    frame = twin.tick()
                    t = START + elapsed
                    assert frame.time == t

                    # SSE delivers every second stepped, in order, even after a jump.
                    while shown < t:
                        delta = await anext(events)
                        shown += 1
                        assert delta["event"] == "delta"
                        assert delta["data"]["time"] == shown
                        assert delta["data"]["events"] == 2
                        assert [f["fault"] for f in delta["data"]["faults"]] == [
                            "crac.compressor_trip",
                            "crac.comm_loss",
                        ]
                        assert HOT_AISLE_DH03 in delta["data"]["points"]
                        assert "temp_c" in delta["data"]["state"]["DH03"]
                        for p, v in delta["data"]["points"].items():
                            seen[p] = v["value"]
                    assert delta["data"]["seq"] == frame.seq

                    await _until(lambda f=frame: services.opc.published_seq == f.seq)
                    rest = (await http.get("/api/points", params={"path": SAMPLE})).json()
                    assert rest["time"] == t

                    for point in rest["points"]:
                        path = point["path"]
                        value = frame.projection.values[path]
                        # REST, SSE and the frame agree exactly ...
                        assert point["value"] == seen[path] == value, path
                        assert point["timestamp"] == iso(t)
                        # ... and so does OPC UA, stamped with the same sim second.
                        # Quality too: a CRAC with lost communication reads Bad everywhere.
                        dv = await nodes[path].read_data_value(raise_on_bad_status=False)
                        quality = frame.projection.quality(path).value
                        assert point["quality"] == quality, path
                        assert dv.StatusCode.name == quality.capitalize(), path
                        opc_value = dv.Value.Value
                        if isinstance(opc_value, datetime):
                            opc_value = int(opc_value.astimezone(UTC).timestamp() * 1000)
                        if quality != "bad":  # a Bad value may arrive empty
                            assert opc_value == value, path
                        assert dv.SourceTimestamp.astimezone(UTC).timestamp() == t

                    rest_state = (await http.get("/api/state/DH03")).json()
                    assert rest_state["time"] == t
                    assert rest_state["state"] == frame.state.assets["DH03"]

                # The faults are visible, and the same, on every surface.
                assert seen["CRAC/L1_CRAC3/Loss of Signal Alarm"] is True
                assert frame.projection.quality(SAMPLE[-2]).value == "bad"
                assert seen[HOT_AISLE_DH03] > snapshot["data"]["points"][HOT_AISLE_DH03]["value"]

                # Reset starts a new epoch, which SSE announces with a fresh snapshot.
                await http.post("/api/reset", json={"confirm": True})
                again = await anext(events)
                assert again["event"] == "snapshot"
                assert again["data"]["epoch"] == 1
                assert again["data"]["events"] == 0

    asyncio.run(exercise())


async def _sse_events(stream: httpx.Response):
    event: dict = {}
    async for line in stream.aiter_lines():
        if line.startswith("event: "):
            event["event"] = line.removeprefix("event: ")
        elif line.startswith("data: "):
            event["data"] = json.loads(line.removeprefix("data: "))
        elif line.startswith("id: "):
            event["id"] = line.removeprefix("id: ")
        elif line == "" and "data" in event:
            yield event
            event = {}
