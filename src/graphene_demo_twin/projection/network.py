"""The control network's points and the communication quality of every point behind it.

A physical switch shows in two export views, `Network Topology/<switch>` and `Network
Switches/<switch>`: both read the one device's state, so they always agree (v1 #2). So do
`Other/Gateway 1|2 Status` and their gateway's `Comm`. A point takes the quality of the
device that reports it; the supervisor's own points (Comm, Status, Loss of Signal) stay good.
"""

from collections.abc import Callable

from graphene_demo_twin.asset_model import AssetModel
from graphene_demo_twin.plant_design import PlantDesign
from graphene_demo_twin.projection.projector import Binding, QualityBinding, VariableRead
from graphene_demo_twin.projection.values import Quality
from graphene_demo_twin.sim import Scalar, WorldState
from graphene_demo_twin.sim.network import (
    FLAP_PORT,
    ROLES,
    SUPERVISORS,
    TOPOLOGY,
    Port,
    network,
    role_of,
)

SUPERVISOR_POINTS = frozenset({"Loss of Signal Alarm"})
"""Members the supervisor computes itself, so they keep good quality when the asset's
communication is lost."""
DEVICE_POINTS = {
    "CPU": "cpu_pct",
    "Memory": "mem_pct",
    "Temperature": "temp_c",
    "Ping Time": "ping_ms",
    "Comm": "comm_lost",
    "Status": "status",
}
KPI_VIEWS = frozenset({"Dashboard", "Other"})
"""KPI views the supervisor itself computes; they follow the SCADA server pair's quality."""
GATEWAY_STATUS = {"Other/Gateway 1 Status": "GATEWAY A", "Other/Gateway 2 Status": "GATEWAY B"}
"""True while the supervisor reaches the gateway."""
VIEW_SYSTEMS = {
    "Chiller System Control": "Cooling",
    "Chiller_System": "Cooling",
    "Environment Monitoring": "Environment",
}
"""The system whose field devices a Plant View point is read from, when it observes no
Unexported Asset of its own: the chiller plant controller's two views sit behind the cooling
gateway, and the hall aggregates behind the environment one. Dashboard and Other are the
supervisor's own KPIs."""


def network_bindings(asset_model: AssetModel, design: PlantDesign) -> list[Binding]:
    net = network(design)
    bindings: list[Binding] = []

    def device(prefix: str, node: str) -> None:
        for name, key in DEVICE_POINTS.items():
            bindings.append(Binding(f"{prefix}/{name}", VariableRead(node, key)))
        bindings.append(Binding(f"{prefix}/Uptime", _uptime_days(node)))

    for d in net.devices:
        device(d, d)
    for view, d in net.switches.items():
        device(view, d)
        ports = net.ports[d]
        bindings.append(Binding(f"{view}/Port Count", _const(len(ports))))
        for label, display in (("Ports Up", 1), ("Ports Down", 2), ("Ports Off", 0)):
            bindings.append(Binding(f"{view}/{label}", _count(d, ports, display)))
        for port in ports:
            at = f"{view}/Ports/Port {port.number:02d}"
            for name, read in _port(d, port).items():
                bindings.append(Binding(f"{at}/{name}", read))
    for path, gw in GATEWAY_STATUS.items():
        if path in asset_model.points:
            bindings.append(Binding(path, VariableRead(f"{TOPOLOGY}/{gw}", "online")))
    return bindings


def network_quality(asset_model: AssetModel, design: PlantDesign) -> list[QualityBinding]:
    """Every point read through the control network takes its reporting device's quality:
    a network device's own, a field device's (its gateway's or worse), and for a Plant View
    the Unexported Asset it observes or else its view's gateway."""
    net = network(design)
    bindings: list[QualityBinding] = []
    by_node: dict[str, str] = {d: d for d in net.devices}
    by_node.update(net.switches)
    for asset, node in by_node.items():
        paths = tuple(
            p.path for p in asset_model.points_of(asset) if p.name not in SUPERVISOR_POINTS
        )
        bindings.append(QualityBinding(paths, _comm(node)))
    # A field device whose own device logic reads its communication (the airside's Loss of
    # Signal) carries its quality in its state.
    for node in net.state_comm:
        if node not in asset_model.assets:
            continue
        paths = tuple(
            p.path for p in asset_model.points_of(node) if p.name not in SUPERVISOR_POINTS
        )
        bindings.append(QualityBinding(paths, _comm(node)))
    # Every other field device: its gateway's quality, or worse where the device itself stops
    # answering polls. The Unexported Assets a Plant View folder observes read the same way.
    field = {
        n: gw
        for n, gw in net.field_gateway.items()
        if n not in net.state_comm and n in asset_model.assets
    }
    for node, gw in field.items():
        paths = tuple(
            p.path for p in asset_model.points_of(node) if p.name not in SUPERVISOR_POINTS
        )
        if paths:
            bindings.append(QualityBinding(paths, _field_comm(node, gw)))

    observers = {
        folder: net.field_gateway[u.id]
        for u in design.unexported.values()
        if u.id in net.field_gateway
        for folder in u.observed_by
    }
    views: dict[str, list[str]] = {}
    for p in asset_model.points.values():
        if p.asset is not None or p.name in SUPERVISOR_POINTS:
            continue
        node = _observer(p.path, observers)
        if node is None:
            gw = net.gateways.get(VIEW_SYSTEMS.get(p.path.split("/", 1)[0], ""))
            node = gw
        if node is not None:
            views.setdefault(node, []).append(p.path)
    bindings += (QualityBinding(tuple(paths), _comm(node)) for node, paths in views.items())

    # A KPI view point belongs to no placed asset of its own: the supervisor computes it from
    # what its SCADA servers collect, so its quality is the pair's.
    pair = SUPERVISORS
    kpi = tuple(
        p.path
        for p in asset_model.points.values()
        if p.asset is None and p.support and p.path.split("/", 1)[0] in KPI_VIEWS
    )
    if kpi:
        bindings.append(QualityBinding(kpi, _pair(pair)))
    return bindings


def _pair(devices: tuple[str, ...]) -> Callable[[WorldState], Quality]:
    """Good while either SCADA server is reachable, uncertain while the pair still serves but
    is degraded, bad only when neither does."""

    def read(state: WorldState) -> Quality:
        return max((Quality(state.assets[d]["comm"]) for d in devices), default=Quality.BAD)

    return read


def _observer(path: str, observers: dict[str, str]) -> str | None:
    cut = path.rfind("/")
    while cut > 0:
        if (node := observers.get(path[:cut])) is not None:
            return node
        cut = path.rfind("/", 0, cut)
    return None


def _comm(node: str) -> Callable[[WorldState], Quality]:
    def read(state: WorldState) -> Quality:
        return Quality(state.assets[node]["comm"])

    return read


def _field_comm(node: str, gateway: str) -> Callable[[WorldState], Quality]:
    """A field device's quality: the gateway's, or worse where its own polls are lost. The
    device's own state may not be modelled at all, in which case it has no variables and only
    a comm-loss fault on it can degrade its points."""

    def read(state: WorldState) -> Quality:
        own = state.assets.get(node)
        loss = own.get("quality.comm_loss", 0.0) if own else 0.0
        if loss >= 0.5:
            return Quality.BAD
        quality = Quality(state.assets[gateway]["comm"])
        if loss > 0.0:
            return Quality.UNCERTAIN
        return quality

    return read


def _const(value: Scalar) -> Callable[[WorldState], Scalar]:
    return lambda state: value


def _uptime_days(node: str) -> Callable[[WorldState], Scalar]:
    def read(state: WorldState) -> Scalar:
        s = state.assets[node]
        return (state.time - s["boot_s"]) // 86_400 if s["up"] else 0

    return read


def _link_up(state: WorldState, switch: str, port: Port) -> bool:
    if port.peer is None:
        return False
    a = state.assets
    s = a[switch]
    if not (s["up"] and a[port.peer]["up"]):
        return False
    return not (s["flap_down"] and port.number == FLAP_PORT)


def _display(state: WorldState, switch: str, port: Port) -> int:
    """The UDT's Display Status: 0 Off (admin down), 1 Up, 2 Down."""
    if port.peer is None:
        return 0
    return 1 if _link_up(state, switch, port) else 2


def _count(switch: str, ports: tuple[Port, ...], display: int) -> Callable[[WorldState], Scalar]:
    return lambda state: sum(_display(state, switch, p) == display for p in ports)


def _port(switch: str, port: Port) -> dict[str, Callable[[WorldState], Scalar]]:
    peer = port.peer
    if peer is None:
        return {
            "Admin Status": _const(2),
            "Description": _const("Spare"),
            "Display Status": _const(0),
            "Error Count": _const(0),
            "In Utilization": _const(0.0),
            "Link Status": _const(2),
            "Out Utilization": _const(0.0),
            "PoE Power": _const(0.0),
            "Speed": _const(0),
        }
    role = ROLES[role_of(peer)]
    poe = 6.5 if role_of(peer) == "kvm" else 0.0
    flaps = port.number == FLAP_PORT

    def up(state: WorldState) -> bool:
        return _link_up(state, switch, port)

    def load(state: WorldState) -> float:
        """How busy the link is: the peer's CPU relative to its idle, as a traffic proxy."""
        return state.assets[peer]["cpu_pct"] / max(role.cpu, 1.0)

    return {
        "Admin Status": _const(1),
        "Description": _const(peer.rsplit("/", 1)[-1]),
        "Display Status": lambda s: _display(s, switch, port),
        "Error Count": (lambda s: s.assets[switch]["flap_errors"]) if flaps else _const(0),
        "In Utilization": lambda s: min(role.link_in * load(s), 100.0) if up(s) else 0.0,
        "Link Status": lambda s: 1 if up(s) else 2,
        "Out Utilization": lambda s: min(role.link_out * load(s), 100.0) if up(s) else 0.0,
        "PoE Power": lambda s: poe if up(s) else 0.0,
        "Speed": lambda s: port.speed if up(s) else 0,
    }
