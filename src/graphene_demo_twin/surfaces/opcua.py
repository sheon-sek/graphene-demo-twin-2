"""The OPC UA surface (ADR-0001): every Asset Model point, read-only, updated once per frame.

Namespace `urn:eetarp:graphene:demo:twin`; each point is a variable with NodeId
`ns=<idx>;s=point:<exportPath>` under folders that mirror the export path, and its
SourceTimestamp is the frame's sim time.
"""

import logging
from datetime import UTC, datetime

from asyncua import Server, ua
from asyncua.common.node import Node

from graphene_demo_twin.projection import Projection, Quality
from graphene_demo_twin.sim import Scalar
from graphene_demo_twin.twin import Frame, Twin

NAMESPACE = "urn:eetarp:graphene:demo:twin"
ENDPOINT_PATH = "/graphene/twin"
SERVER_NAME = "Graphene Demo Twin"

VARIANT_TYPES: dict[str, ua.VariantType] = {
    "Boolean": ua.VariantType.Boolean,
    "Float4": ua.VariantType.Float,
    "Float8": ua.VariantType.Double,
    "Int4": ua.VariantType.Int32,
    "Int8": ua.VariantType.Int64,
    "String": ua.VariantType.String,
    "DateTime": ua.VariantType.DateTime,
    "DataSet": ua.VariantType.String,
    "Document": ua.VariantType.String,
}
"""Ignition data type → OPC UA built-in type. DataSet and Document travel as JSON text."""

_STATUS = {
    Quality.GOOD: ua.StatusCode(ua.StatusCodes.Good),
    Quality.UNCERTAIN: ua.StatusCode(ua.StatusCodes.Uncertain),
    Quality.BAD: ua.StatusCode(ua.StatusCodes.Bad),
}


def point_node_id(path: str, namespace_index: int) -> ua.NodeId:
    return ua.NodeId(f"point:{path}", namespace_index)


def endpoint_url(host: str, port: int) -> str:
    return f"opc.tcp://{host}:{port}{ENDPOINT_PATH}"


class OpcUaSurface:
    """An asyncua server publishing the twin's frames."""

    def __init__(self, twin: Twin, host: str, port: int) -> None:
        self.twin = twin
        self.endpoint = endpoint_url(host, port)
        self.namespace_index: int | None = None
        self.published_seq = -1
        """`seq` of the last frame written to the address space."""
        self._server: Server | None = None
        self._written: Projection | None = None
        self._variables: dict[str, tuple[ua.NodeId, ua.VariantType]] = {}

    @property
    def variables(self) -> dict[str, ua.NodeId]:
        return {path: node_id for path, (node_id, _) in self._variables.items()}

    async def start(self) -> None:
        # Security policy None is the contract (LAN demo tool); asyncua warns about it at startup.
        logging.getLogger("asyncua").setLevel(logging.ERROR)
        server = Server()
        await server.init()
        server.set_endpoint(self.endpoint)
        server.set_server_name(SERVER_NAME)
        server.set_security_policy([ua.SecurityPolicyType.NoSecurity])
        self.namespace_index = await server.register_namespace(NAMESPACE)
        self._server = server
        await self._build()
        await self.publish(self.twin.frame)
        await server.start()

    async def run(self) -> None:
        """Write every new frame until the twin closes."""
        while (frame := await self.twin.next_frame(self.published_seq)) is not None:
            await self.publish(frame)

    async def publish(self, frame: Frame) -> None:
        assert self._server is not None
        if frame.projection is self._written:  # same step, only the Event Log changed
            self.published_seq = frame.seq
            return
        stamp = datetime.fromtimestamp(frame.time, UTC)
        projection = frame.projection
        for path, (node_id, variant_type) in self._variables.items():
            value = _encode(projection.values[path], variant_type)
            await self._server.write_attribute_value(
                node_id,
                ua.DataValue(
                    ua.Variant(value, variant_type),
                    _STATUS[projection.quality(path)],
                    SourceTimestamp=stamp,
                    ServerTimestamp=stamp,
                ),
            )
        self._written = projection
        self.published_seq = frame.seq

    async def stop(self) -> None:
        if self._server is not None:
            await self._server.stop()
            self._server = None

    async def _build(self) -> None:
        assert self._server is not None and self.namespace_index is not None
        idx = self.namespace_index
        folders: dict[str, Node] = {"": self._server.nodes.objects}
        for point in self.twin.asset_model.points.values():
            *parents, name = point.path.split("/")
            parent = ""
            for segment in parents:
                key = f"{parent}/{segment}" if parent else segment
                if key not in folders:
                    folders[key] = await folders[parent].add_folder(
                        ua.NodeId(f"folder:{key}", idx), ua.QualifiedName(segment, idx)
                    )
                parent = key
            variant_type = VARIANT_TYPES[point.data_type]
            zero = _encode(self.twin.frame.projection.values[point.path], variant_type)
            # A QualifiedName, not a string: asyncua would read a ':' in a name
            # ("AC Voltage: L2-N") as a namespace separator.
            node = await folders[parent].add_variable(
                point_node_id(point.path, idx),
                ua.QualifiedName(name, idx),
                zero,
                varianttype=variant_type,
            )
            self._variables[point.path] = (node.nodeid, variant_type)


def _encode(value: Scalar, variant_type: ua.VariantType) -> object:
    if variant_type is ua.VariantType.DateTime:
        return datetime.fromtimestamp(value / 1000.0, UTC)
    return value
