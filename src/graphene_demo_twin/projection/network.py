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
    Network,
    Port,
    network,
    patched_key,
    port_drops_key,
    role_of,
)

SUPERVISOR_POINTS = frozenset({"Loss of Signal Alarm"})
"""Members the supervisor computes itself, so they keep good quality when the asset's
communication is lost."""
DEVICE_HEALTH_POINTS = frozenset({"Comm", "Status"})
"""A network device's health as the supervisor judges it: good quality and never stale-held,
so a lost device reads Disconnected rather than its last healthy values."""
QUALITY_RANK = (Quality.GOOD, Quality.UNCERTAIN, Quality.BAD)
"""Qualities from best to worst."""
DEVICE_POINTS = {
    "CPU": "cpu_pct",
    "Memory": "mem_pct",
    "Temperature": "temp_c",
    "Ping Time": "ping_ms",
    "Comm": "comm_lost",
    "Status": "status",
}
PATCHED_POE_W = 7.0
"""PoE draw of the temporary access point an operator patches into a spare port."""
PATCHED_IN, PATCHED_OUT = 3.0, 8.0
"""Utilization % of a patched spare port, into and out of the switch."""
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
            bindings.append(Binding(f"{view}/{label}", _count(net, view, d, ports, display)))
        for port in ports:
            at = f"{view}/Ports/Port {port.number:02d}"
            for name, read in _port(net, view, d, port).items():
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
    exempt = SUPERVISOR_POINTS | DEVICE_HEALTH_POINTS
    for asset, node in by_node.items():
        paths = tuple(p.path for p in asset_model.points_of(asset) if p.name not in exempt)
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

    # A KPI view point is the supervisor's own computation from what its SCADA servers
    # collect, so its quality is the pair's; the gateway status points are its reachability.
    kpi = tuple(
        p.path
        for p in asset_model.points.values()
        if p.path.split("/", 1)[0] in KPI_VIEWS and p.path not in GATEWAY_STATUS
    )
    if kpi:
        bindings.append(QualityBinding(kpi, _pair(SUPERVISORS)))
    return bindings


def _pair(devices: tuple[str, ...]) -> Callable[[WorldState], Quality]:
    """Good while either SCADA server is reachable, uncertain while the pair still serves but
    is degraded, bad only when neither does."""

    def read(state: WorldState) -> Quality:
        return min(
            (Quality(state.assets[d]["comm"]) for d in devices),
            key=QUALITY_RANK.index,
            default=Quality.BAD,
        )

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
    """A field device's quality: the worse of the gateway's and its own polls'. The
    device's own state may not be modelled at all, in which case it has no variables and only
    a comm-loss fault on it can degrade its points."""

    def read(state: WorldState) -> Quality:
        own = state.assets.get(node)
        loss = own.get("quality.comm_loss", 0.0) if own else 0.0
        own_q = Quality.BAD if loss >= 0.5 else Quality.UNCERTAIN if loss > 0.0 else Quality.GOOD
        return max(own_q, Quality(state.assets[gateway]["comm"]), key=QUALITY_RANK.index)

    return read


def _errors(switch: str, peer: str, flaps: bool) -> Callable[[WorldState], Scalar]:
    """A port's error counter: one per drop of its link, from its peer failing or, on the
    flapping port, from the flap."""
    if flaps:
        return lambda s: s.assets[peer]["drops"] + s.assets[switch]["flap_errors"]
    return lambda s: s.assets[peer]["drops"]


def _const(value: Scalar) -> Callable[[WorldState], Scalar]:
    return lambda state: value


def _uptime_days(node: str) -> Callable[[WorldState], Scalar]:
    def read(state: WorldState) -> Scalar:
        s = state.assets[node]
        return (state.time - s["boot_s"]) // 86_400 if s["up"] else 0

    return read


def _link_up(
    state: WorldState, view: str, switch: str, port: Port, flappers: tuple[str, ...]
) -> bool:
    """One state per physical link, the same from both ends: down with either device, or
    while a switch whose flapping port it is has it dropped. A spare port's link is up while
    a device is patched into it and the switch is up."""
    a = state.assets
    if port.peer is None:
        return a[view][patched_key(port.number)] and a[switch]["up"]
    if not (a[switch]["up"] and a[port.peer]["up"]):
        return False
    return not any(a[f]["flap_down"] for f in flappers)


def _flappers(net: Network, switch: str, port: Port) -> tuple[str, ...]:
    """The switches whose flapping port carries this port's link."""
    if port.peer is None:
        return ()
    link = frozenset((switch, port.peer))
    return tuple(sw for sw, flap in net.flap_link.items() if frozenset(flap) == link)


def _display(
    state: WorldState, view: str, switch: str, port: Port, flappers: tuple[str, ...]
) -> int:
    """The UDT's Display Status: 0 Off (admin down), 1 Up, 2 Down."""
    if port.peer is None and not state.assets[view][patched_key(port.number)]:
        return 0
    return 1 if _link_up(state, view, switch, port, flappers) else 2


def _count(
    net: Network, view: str, switch: str, ports: tuple[Port, ...], display: int
) -> Callable[[WorldState], Scalar]:
    flappers = [_flappers(net, switch, p) for p in ports]
    return lambda state: sum(
        _display(state, view, switch, p, f) == display for p, f in zip(ports, flappers, strict=True)
    )


def _spare_port(view: str, switch: str, port: Port) -> dict[str, Callable[[WorldState], Scalar]]:
    """A port nothing in the Plant Design connects: admin down and empty until an operator
    patches a temporary PoE access point into it."""
    patched, drops = patched_key(port.number), port_drops_key(port.number)

    def up(state: WorldState) -> bool:
        return _link_up(state, view, switch, port, ())

    return {
        "Admin Status": lambda s: 1 if s.assets[view][patched] else 2,
        "Description": _const("Spare"),
        "Display Status": lambda s: _display(s, view, switch, port, ()),
        "Error Count": lambda s: s.assets[view][drops],
        "In Utilization": lambda s: PATCHED_IN if up(s) else 0.0,
        "Link Status": lambda s: 1 if up(s) else 2,
        "Out Utilization": lambda s: PATCHED_OUT if up(s) else 0.0,
        "PoE Power": lambda s: PATCHED_POE_W if up(s) else 0.0,
        "Speed": lambda s: 1_000 if up(s) else 0,
    }


def _port(
    net: Network, view: str, switch: str, port: Port
) -> dict[str, Callable[[WorldState], Scalar]]:
    peer = port.peer
    if peer is None:
        return _spare_port(view, switch, port)
    role = ROLES[role_of(peer)]
    poe = 6.5 if role_of(peer) == "kvm" else 0.0
    flaps = port.number == FLAP_PORT and switch in net.flap_link
    flappers = _flappers(net, switch, port)

    def up(state: WorldState) -> bool:
        return _link_up(state, view, switch, port, flappers)

    def load(state: WorldState) -> float:
        """How busy the link is: the peer's CPU relative to its idle, as a traffic proxy."""
        return state.assets[peer]["cpu_pct"] / max(role.cpu, 1.0)

    return {
        "Admin Status": _const(1),
        "Description": _const(peer.rsplit("/", 1)[-1]),
        "Display Status": lambda s: _display(s, view, switch, port, flappers),
        "Error Count": _errors(switch, peer, flaps),
        "In Utilization": lambda s: min(role.link_in * load(s), 100.0) if up(s) else 0.0,
        "Link Status": lambda s: 1 if up(s) else 2,
        "Out Utilization": lambda s: min(role.link_out * load(s), 100.0) if up(s) else 0.0,
        "PoE Power": lambda s: poe if up(s) else 0.0,
        "Speed": lambda s: port.speed if up(s) else 0,
    }
