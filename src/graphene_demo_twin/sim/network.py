"""The BMS control network (P4): core and distribution switches, field gateways, servers and
workstations, and the communication quality of every field device behind them.

The supervisor (the redundant SCADA pair, DATABASE SERVER A and B) polls the field devices
through the gateways, and the gateways through the switches, over the Plant Design's `net`
connections. A device's `comm` is the best quality over any path from an up supervisor
server: `good`, `uncertain` (a lossy device or a flapping link on the way) or `bad` (no path
at all). Each gateway carries the systems the Plant Design assigns it, so a field device's
`comm` is its gateway's, or worse where the device itself stops answering. The physical world
keeps running underneath: nothing here feeds back into physics.

Faults act through variables on the network devices: `quality.device_failure` (a switch or
gateway dies, and reboots on Clear), `quality.comm_loss` (a device stops forwarding),
`quality.port_flap` (on a `Network Switches/` view: its first uplink port flaps),
`constraint.cpu_overload` (a server runs out of CPU) and `observation.clock_drift_s_per_h`
(the NTP server's clock drifts).

A port the Plant Design leaves unconnected (a spare) is admin down with nothing in it. An
Operator Command on the switch's `Network Switches/` view patches a temporary PoE device (a
technician's wireless access point) into a spare port, which admin-enables it; its link then
follows the switch, and each drop of that link counts one error on the port.
"""

import functools
import math
from dataclasses import dataclass

from graphene_demo_twin.plant_design import ConnectionKind, PlantDesign
from graphene_demo_twin.sim.commands import COMMAND, CommandSpec, command_problem
from graphene_demo_twin.sim.engine import StepContext
from graphene_demo_twin.sim.events import Event
from graphene_demo_twin.sim.state import AssetState, WorldState

TOPOLOGY = "Network Topology"
SWITCH_VIEW = "Network Switches"
"""Port-level view of the same four physical switches as their `Network Topology/` entries."""
SUPERVISORS = (f"{TOPOLOGY}/DATABASE SERVER A", f"{TOPOLOGY}/DATABASE SERVER B")
"""The redundant SCADA server pair: every poll starts at whichever of them is up."""
NTP = f"{TOPOLOGY}/NTP SERVER A"
PORTS = 48
POLL_S = 10
"""How often device health (CPU, memory, temperature, ping) is polled; a quiet network's
gauges move slowly, so the pass runs on this cadence rather than every second."""
UPLINK_PORTS = range(41, 47)
"""10 Gb/s ports that take links to other switches and gateways, in authored order."""
FLAP_PORT = UPLINK_PORTS.start
"""The port a port flap acts on: the switch's first uplink."""
BOOT_AGE_S = 180 * 86_400
"""Uptime at the start: 180 days (the export's value) plus up to 30 days per device."""
FLAP_CYCLE_S = 20
FLAP_ERRORS_PER_S = 7

GOOD, UNCERTAIN, BAD = 0, 1, 2
"""Quality as the supervisor sees it: over every path, of the worst device or link on it."""
QUALITY = ("good", "uncertain", "bad")


@dataclass(frozen=True, slots=True)
class Role:
    cpu: float
    """CPU % at idle."""
    mem: float
    rise_c: float
    """Internal temperature above the room air at idle."""
    ping_ms: float
    """Round trip per hop from the supervisor."""
    link_in: float
    link_out: float
    """Utilization % of the switch port that links the device, into and out of the switch."""


ROLES = {
    "switch": Role(18.0, 38.0, 22.0, 0.6, 14.0, 6.0),
    "gateway": Role(22.0, 45.0, 20.0, 1.8, 24.0, 9.0),
    "server": Role(20.0, 55.0, 18.0, 0.5, 32.0, 21.0),
    "workstation": Role(10.0, 40.0, 15.0, 0.7, 5.0, 12.0),
    "kvm": Role(5.0, 20.0, 12.0, 1.0, 18.0, 2.0),
}
GATEWAY_POLL_CPU = 20.0
"""Extra CPU a gateway spends while the supervisor polls its field devices."""
SCAN_CPU = 15.0
"""Extra CPU a supervisor server spends running the scan."""
FLAP_CPU = 25.0
"""Extra CPU a switch spends recomputing its topology while a port flaps."""
OVERLOAD_PING_MS = 250.0
SWITCH_TYPE = "Network Switch"
PORT_COMMANDS: tuple[CommandSpec, ...] = (
    CommandSpec("patch", "Patch a device into spare port", "", "number", minimum=1, maximum=PORTS),
    CommandSpec("unpatch", "Unplug spare port", "", "number", minimum=1, maximum=PORTS),
)
"""Operator Commands on a `Network Switches/` view: the value is a spare port's number."""


def patched_key(port: int) -> str:
    """The switch view's variable that holds whether a device is patched into spare `port`."""
    return f"port_{port:02d}_patched"


def port_drops_key(port: int) -> str:
    """The switch view's variable that counts the link drops on spare `port`."""
    return f"port_{port:02d}_drops"


def role_of(path: str) -> str:
    name = path.rsplit("/", 1)[-1]
    if "SWITCH" in name:
        return "switch"
    if name.startswith("GATEWAY"):
        return "gateway"
    if "SERVER" in name:
        return "server"
    if name.startswith("KVM"):
        return "kvm"
    return "workstation"


@dataclass(frozen=True, slots=True)
class Port:
    number: int
    peer: str | None
    """The Network Topology device the port links, or None for an unused port."""
    speed: int
    """Negotiated speed in Mb/s while the link is up."""


@dataclass(frozen=True, slots=True)
class Network:
    """The control network as the Plant Design authors it, derived once per design."""

    devices: tuple[str, ...]
    """Every Network Topology device."""
    links: tuple[tuple[str, str], ...]
    """Net connections between devices; traffic flows either way."""
    switches: dict[str, str]
    """Network Switches view → the Network Topology device it shows."""
    ports: dict[str, tuple[Port, ...]]
    """Network Topology switch → its 48 ports."""
    flap_link: dict[str, tuple[str, str]]
    """Network Topology switch → the link on the port that flaps (its first uplink)."""
    gateways: dict[str, str]
    """System → the gateway carrying it."""
    field: dict[str, tuple[str, ...]]
    """Gateway → every field node behind it (placed assets and Unexported Assets)."""
    field_gateway: dict[str, str]
    """Field node → the gateway carrying it."""
    state_comm: frozenset[str]
    """Field nodes whose AssetState carries `comm`, because their own device logic reads it
    (the airside's Loss of Signal). Every other field device's quality is derived at
    projection from its gateway's and its own comm-loss level."""
    view_of: dict[str, str]
    """Network Topology switch → its Network Switches view."""
    rooms: dict[str, str]
    """Device → the room it stands in."""


@functools.cache
def network(design: PlantDesign) -> Network:
    devices = tuple(
        a.path for a in design.assets.values() if a.type_id == "Network Device" and a.room
    )
    on = set(devices)
    links = tuple(
        (c.source, c.target)
        for c in design.connections
        if c.kind is ConnectionKind.NET and c.source in on and c.target in on
    )
    switches = {
        a.path: f"{TOPOLOGY}/{a.path.split('/', 1)[1]}"
        for a in design.assets.values()
        if a.type_id == "Network Switch" and a.room
    }
    ports: dict[str, tuple[Port, ...]] = {}
    flap_link: dict[str, tuple[str, str]] = {}
    for device in switches.values():
        peers = [*design.downstream(device, ConnectionKind.NET)]
        peers += [p for p in design.upstream(device, ConnectionKind.NET) if p not in peers]
        uplinks = [p for p in peers if role_of(p) in ("switch", "gateway")]
        access = [p for p in peers if p not in uplinks]
        if len(uplinks) > len(UPLINK_PORTS) or len(access) > UPLINK_PORTS.start - 1:
            raise ValueError(f"{device} has more links than ports")
        plan = {n: Port(n, None, 0) for n in range(1, PORTS + 1)}
        for n, peer in zip(UPLINK_PORTS, uplinks, strict=False):
            plan[n] = Port(n, peer, 10_000)
        for n, peer in enumerate(access, start=1):
            plan[n] = Port(n, peer, 1_000)
        ports[device] = tuple(plan.values())
        if plan[FLAP_PORT].peer is not None:
            flap_link[device] = (device, plan[FLAP_PORT].peer)
    gateways = {
        system: gw for gw, systems in design.gateways.items() for system in systems if gw in on
    }
    field: dict[str, list[str]] = {gw: [] for gw in design.gateways}
    for a in design.assets.values():
        gw = gateways.get(a.system)
        if gw is not None and a.room and not a.support:
            field[gw].append(a.path)
    return Network(
        devices=devices,
        links=links,
        switches=switches,
        ports=ports,
        flap_link=flap_link,
        gateways=gateways,
        field={gw: tuple(nodes) for gw, nodes in field.items()},
        field_gateway={n: gw for gw, nodes in field.items() for n in nodes},
        state_comm=frozenset(
            n
            for nodes in field.values()
            for n in nodes
            if n in design.assets
            and design.assets[n].type_id
            in ("CRAC", "PAHU", "FCU", "FWU", "CDU", "Ceiling Cooling Units")
        ),
        view_of={d: view for view, d in switches.items()},
        rooms={d: design.assets[d].room or "" for d in devices},
    )


class NetworkDomain:
    """Reachability and device health over the control network; see the module docstring."""

    settling_s = 0
    commands = {SWITCH_TYPE: PORT_COMMANDS}

    def initial(self, ctx: StepContext) -> dict[str, AssetState]:
        net = network(ctx.design)
        state: dict[str, AssetState] = {}
        for d in net.devices:
            jitter = round(ctx.noise.held(f"net.boot.{d}", 0) * 30) * 86_400
            state[d] = {
                "up": True,
                "comm": "good",
                "comm_lost": 0,
                "online": True,
                "status": 0,
                "boot_s": ctx.time - BOOT_AGE_S - jitter,
                "cpu_pct": 0.0,
                "mem_pct": 0.0,
                "temp_c": 0.0,
                "ping_ms": 0.0,
                "clock_offset_s": 0.0,
                "flap_down": False,
                "flap_errors": 0,
                "drops": 0,
            }
        for n in net.state_comm:
            state[n] = {"comm": "good"}
        for view, d in net.switches.items():  # the port view carries its fault and patches
            state[view] = {"comm": "good"}
            for port in spare_ports(net, d):
                state[view][patched_key(port)] = False
                state[view][port_drops_key(port)] = 0
        return state

    def complete(self, state: WorldState, ctx: StepContext) -> None:
        # The initial state is live: poll every gauge once, whatever the cadence would say.
        self._metrics(state, ctx, network(ctx.design), _reach(state, network(ctx.design)), True)

    def handles(self, event: Event, design: PlantDesign) -> bool:
        net = network(design)
        switch = net.switches.get(event.target)
        if (
            event.kind != COMMAND
            or switch is None
            or command_problem(PORT_COMMANDS, event.params) is not None
        ):
            return False
        port = event.params["value"]
        return port == int(port) and int(port) in spare_ports(net, switch)

    def apply(self, event: Event, state: WorldState) -> None:
        view = state.assets[event.target]
        port = int(event.params["value"])
        patch = event.params["command"] == "patch"
        if view[patched_key(port)] and not patch:
            view[port_drops_key(port)] += 1  # unplugging drops the link
        view[patched_key(port)] = patch

    def step(self, state: WorldState, ctx: StepContext) -> None:
        """Every step: device health and, from it, what the supervisor can reach. The pass is
        cheap (a few dozen devices) and keeps the Live World and every fork bit-identical."""
        a, net = state.assets, network(ctx.design)
        t = ctx.time
        for d in net.devices:
            s = a[d]
            up = s.get("quality.device_failure", 0.0) < 0.5
            if up and not s["up"]:
                s["boot_s"] = t  # rebooted
            elif s["up"] and not up:
                s["drops"] += 1  # every link to it drops once, and each port counts it
                if view := net.view_of.get(d):
                    v = a[view]
                    for port in spare_ports(net, d):
                        if v[patched_key(port)]:
                            v[port_drops_key(port)] += 1
            s["up"] = up
            drift = s.get("observation.clock_drift_s_per_h", 0.0)
            s["clock_offset_s"] = s["clock_offset_s"] + drift * ctx.dt / 3600 if drift else 0.0
        for switch in net.flap_link:
            s = a[switch]
            level = a[net.view_of[switch]].get("quality.port_flap", 0.0)
            down = level > 0.0 and (t % FLAP_CYCLE_S) < math.ceil(FLAP_CYCLE_S * level)
            if down and not s["flap_down"]:
                s["flap_errors"] += 1  # one counter bump per drop, like a real port
            s["flap_down"] = down

        best = _reach(state, net)
        self._metrics(state, ctx, net, best)

        # The field devices whose own logic reads their communication (the airside's Loss of
        # Signal): their gateway's quality, or worse where they stop answering. Every other
        # field device's quality is derived at projection, from its gateway's and its own
        # comm-loss level, so none of them costs state here.
        for n in net.state_comm:
            gw = best.get(net.field_gateway[n], BAD)
            loss = a[n].get("quality.comm_loss", 0.0)
            worst = max(gw, BAD if loss >= 0.5 else UNCERTAIN if loss > 0.0 else GOOD)
            a[n]["comm"] = QUALITY[worst]

    def _metrics(
        self,
        state: WorldState,
        ctx: StepContext,
        net: Network,
        best: dict[str, int] | None,
        force: bool = False,
    ) -> None:
        """Poll every device's health and status; with `best` also take its new reachability.

        The gauges move on their `POLL_S` cadence, not every second — a quiet network's CPU
        and temperature drift slowly — while quality follows reachability every step, so a
        device stops answering the moment a path fails."""
        a = state.assets
        t = ctx.time
        for d in net.devices:
            s = a[d]
            role = ROLES[role_of(d)]
            q = QUALITY.index(s["comm"]) if best is None else best[d]
            s["comm"] = QUALITY[q]  # quality tracks the path every step
            if not force and t % POLL_S and q == GOOD and s["up"] is not False:
                continue
            wobble = ctx.noise.smooth(f"net.{d}", t, 600)
            room = a.get(net.rooms[d], {}).get("temp_c", 22.0)
            if not s["up"]:
                cpu = mem = 0.0
                temp, ping = room, 0.0
            else:
                overload = s.get("constraint.cpu_overload", 0.0)
                cpu = role.cpu + 4.0 * wobble
                if d in net.field:  # a gateway, busy while the supervisor polls it
                    cpu += GATEWAY_POLL_CPU if q < BAD else 0.0
                elif d in SUPERVISORS:
                    cpu += SCAN_CPU
                if view := net.view_of.get(d):
                    cpu += FLAP_CPU * a[view].get("quality.port_flap", 0.0)
                cpu = min(cpu + overload * (100.0 - cpu), 100.0)
                mem = min(role.mem + 3.0 * wobble + 40.0 * overload, 99.0)
                temp = room + role.rise_c + 0.08 * cpu
                ping = role.ping_ms * (1.0 + 0.2 * wobble) * _hops(ctx.design, d)
                ping += OVERLOAD_PING_MS * overload + (40.0 if q == UNCERTAIN else 0.0)
            s["cpu_pct"], s["mem_pct"], s["temp_c"], s["ping_ms"] = cpu, mem, temp, ping
            if best is not None:
                s["comm_lost"] = int(q == BAD)
                s["online"] = q < BAD
            warning = (
                q == UNCERTAIN
                or cpu >= 90.0
                or mem >= 90.0
                or temp >= 65.0
                or abs(s["clock_offset_s"]) >= 1.0
                or (view := net.view_of.get(d)) is not None
                and a[view].get("quality.port_flap", 0.0) > 0.0
            )
            s["status"] = 1 if q == BAD else 2 if warning else 0


def spare_ports(net: Network, switch: str) -> tuple[int, ...]:
    """The numbers of the Network Topology switch's ports that the Plant Design leaves
    unconnected."""
    return tuple(p.number for p in net.ports[switch] if p.peer is None)


def _reach(state: WorldState, net: Network) -> dict[str, int]:
    """Each device's quality as the supervisor sees it: the best over every path from an up
    supervisor server of the worst device or link on the way."""
    a = state.assets
    node_q: dict[str, int] = {}
    for d in net.devices:
        s = a[d]
        loss = s.get("quality.comm_loss", 0.0)
        down = not s["up"] or loss >= 0.5
        node_q[d] = BAD if down else UNCERTAIN if loss > 0.0 else GOOD
    flapping = {frozenset(link) for switch, link in net.flap_link.items() if a[switch]["flap_down"]}
    best = {d: BAD for d in net.devices}
    for root in SUPERVISORS:
        if root in best:
            q = node_q[root]
            if a[root].get("cpu_pct", 0.0) >= 95.0:
                q = max(q, UNCERTAIN)  # its scan overruns: every value it serves is stale
            best[root] = min(best[root], q)
    changed = True
    while changed:
        changed = False
        for x, y in net.links:
            link_q = UNCERTAIN if frozenset((x, y)) in flapping else GOOD
            for u, v in ((x, y), (y, x)):
                if best[u] < BAD and node_q[u] < BAD:
                    candidate = max(best[u], link_q, node_q[v])
                    if candidate < best[v]:
                        best[v] = candidate
                        changed = True
    return best


@functools.cache
def _hops_from_supervisors(design: PlantDesign) -> dict[str, int]:
    net = network(design)
    hops = {s: 1 for s in SUPERVISORS if s in net.rooms}
    frontier = list(hops)
    while frontier:
        nxt = []
        for x, y in net.links:
            for u, v in ((x, y), (y, x)):
                if u in frontier and v not in hops:
                    hops[v] = hops[u] + 1
                    nxt.append(v)
        frontier = nxt
    return hops


def _hops(design: PlantDesign, device: str) -> int:
    return _hops_from_supervisors(design).get(device, 1)
