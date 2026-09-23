"""The electrical network: power flow over the Plant Design `power` graph (#19).

Sources: four utility incomers, each through a 5 MVA transformer onto the main bus of MSB A or
B, and six gensets, three per side (N+1), which an ATS switches onto that bus. Below the two
main buses the network is a tree of boards and meters down to the loads, with two exceptions:
the L1 DB, which its own ATS feeds from either side, and each Data Hall's IT equipment, which
draws from three UPS branches at once (distributed redundancy).

Each step the domain
1. flows this step's loads up the tree to the sources, with the switching the last step left:
   every meter reads the sum of what it feeds, and a transformer or UPS adds its losses;
2. advances the equipment: UPS batteries, genset engines and their fuel;
3. switches: the ATS and genset Controllers act, breakers trip, UPS change mode, and every
   node's `live` flag is set for the next step.

Loads read `powered()` (the `live` flag of whatever feeds them) during their own step, so a
switching change reaches them one step (1 s) later, and every step's flow balances exactly
against the power the loads report. Faults act only through `constraint.*` variables.
"""

import functools
import hashlib
import math
from collections.abc import Mapping
from dataclasses import dataclass

from graphene_demo_twin.plant_design import ConnectionKind, PlantDesign
from graphene_demo_twin.sim.engine import StepContext
from graphene_demo_twin.sim.events import Event
from graphene_demo_twin.sim.state import AssetState, WorldState

POWER = ConnectionKind.POWER
METER_TYPES = frozenset({"GPM96", "GPQM96", "GPQM144", "GEM230", "GEM630"})
SINGLE_PHASE_TYPES = frozenset({"GEM230"})
UPS_TYPE, BRANCH_TYPE, GENSET_TYPE, TANK_TYPE = "UPS", "BCPM", "Genset", "Diesel"

V_LN, V_LL, HV_LL, HZ = 230.0, 400.0, 22_000.0, 50.0

TX_KVA = 5000.0
TX_NO_LOAD_KW, TX_FULL_LOAD_KW = 4.5, 40.0
TX_MAGNETISING_KVAR, TX_REACTANCE = 25.0, 0.06
TX_TAP, TX_DROP = 0.025, 0.045
"""Off-load tap boost, and the LV voltage drop at rated load, per unit."""

GENSET_KW = 2400.0
"""3,000 kVA at 0.8 power factor."""
RATED_RPM, CRANK_RPM = 1500.0, 150.0
CONFIRM_S = 1
"""Utility must be gone this long before the ATS Controller starts the gensets."""
CRANK_S, OVERCRANK_S, RUN_UP_S = 3, 15, 5
COOLDOWN_S = 300
RETRANSFER_S = 120
"""Utility must be back and stable this long before the ATS returns the bus to it."""
TRANSFER_LIMIT_S = 15
"""The design's ATS transfer time: utility lost to bus on gensets."""
DIP_RPM_PER_PU = 120.0
"""Speed lost per unit of block load taken at once (about 4 Hz for a full-rating step)."""
DIP_V_PER_PU = 0.2
DROOP_HZ, DROOP_V = 0.01, 0.02
"""Speed and voltage droop between no load and full load, as fractions of rated."""
JACKET_C, COOLANT_TAU_S = 45.0, 120.0
DAY_TANK_L = 1000.0
DAY_START, DAY_STOP = 0.8, 0.9
"""The fuel pump starts when a day tank falls below DAY_START and fills it to DAY_STOP."""
BULK_TANK_L, BULK_INITIAL_L = 50_000.0, 45_000.0
PUMP_LPM, PUMP_KW, PLC_KW = 40.0, 4.0, 0.5

UPS_KVA, UPS_KW = 500.0, 450.0
CONTROL_UPS_KVA, CONTROL_UPS_KW = 100.0, 90.0
CONTROL_LOAD_KW = 45.0
"""BMS control room and network load the control UPS carries."""
UPS_NO_LOAD_KW, CONTROL_UPS_NO_LOAD_KW, UPS_LOSS, BYPASS_LOSS = 2.0, 0.6, 0.035, 0.005
BATTERY_MIN, CONTROL_BATTERY_MIN = 8.0, 15.0
"""Battery autonomy at the rated kW."""
CHARGE_C, TAPER_SOC, CHARGE_EFFICIENCY = 1.2, 0.97, 0.95
"""Charger limit in capacities per hour, the state of charge its taper starts at, and the
share of charging power the battery stores."""
OVERLOAD_PCT, BYPASS_RETURN_PCT = 110.0, 95.0
DB_TRANSFER_S, DB_RETRANSFER_S = 2, 60

IT_TAN = math.tan(math.acos(0.98))
"""IT power supplies behind the UPS."""
UPS_INPUT_TAN = math.tan(math.acos(0.99))
"""Active front-end rectifier."""


@dataclass(frozen=True, slots=True)
class LoadProfile:
    tan_phi: float
    thd_pct: float
    """Current distortion the load draws."""


def _profile(pf: float, thd: float) -> LoadProfile:
    return LoadProfile(math.tan(math.acos(pf)), thd)


PROFILES: Mapping[str, LoadProfile] = {
    "Chiller": _profile(0.96, 6.0),
    "Chiller Pump": _profile(0.95, 35.0),
    "Cooling Tower": _profile(0.95, 35.0),
    "Makeup Water Pump": _profile(0.95, 35.0),
    "CW Transfer Pump": _profile(0.86, 8.0),
    "CW Booster Pump": _profile(0.95, 35.0),
    "AC Makeup Pump": _profile(0.86, 8.0),
    "CDU": _profile(0.95, 35.0),
    "CRAC": _profile(0.90, 20.0),
    "PAHU": _profile(0.97, 28.0),
    "FWU": _profile(0.97, 28.0),
    "FCU": _profile(0.97, 28.0),
    "Ceiling Cooling Units": _profile(0.97, 28.0),
    "IPS": _profile(0.90, 12.0),
    "RCMS": _profile(0.90, 12.0),
    TANK_TYPE: _profile(0.85, 8.0),
    UPS_TYPE: _profile(0.99, 3.0),
}
ROOM_PROFILE = _profile(0.93, 18.0)
"""LED lighting and small power."""
STAND_IN_PROFILE = _profile(0.85, 25.0)
"""Lift drives and genset jacket heaters and chargers, beyond Meter14 and Meter16."""
IT_BRANCH_THD = 8.0


@dataclass(frozen=True, slots=True)
class Bus:
    """A main bus: MSB A or B, fed from its utility incomers or, through its ATS, its gensets."""

    node: str
    incomers: tuple[str, ...]
    gensets: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Board:
    """A board its own ATS feeds from either of two others (the L1 DB)."""

    node: str
    preferred: str
    alternate: str


@dataclass(frozen=True, slots=True)
class Feed:
    """What one meter feeds, by how its draw is known."""

    meter: str
    loads: tuple[tuple[str, float], ...]
    """(load, tan φ): consumers reporting `power_kw`."""
    metered: tuple[str, ...]
    """Meters and UPS below it, which report `p_kw` and `q_kvar`."""
    boards: tuple[str, ...]
    """Transfer boards it feeds while they are on its side: a board's `fed_by` is the side
    that supplied it during the step, its `source` the side its ATS is on now."""
    stand_in: bool
    """A meter with nothing authored below it: it reports the draw beyond it as `power_kw`."""


class Network:
    """The power graph read once from the Plant Design."""

    def __init__(self, design: PlantDesign) -> None:
        assets = design.assets

        def type_of(node: str) -> str | None:
            placed = assets.get(node)
            return placed.type_id if placed is not None and placed.room else None

        def of_type(type_id: str) -> tuple[str, ...]:
            return tuple(a.path for a in assets.values() if a.type_id == type_id and a.room)

        self.incomers: tuple[str, ...] = tuple(
            a.path
            for a in assets.values()
            if a.room
            and a.type_id in METER_TYPES
            and not design.upstream(a.path, POWER)
            and design.downstream(a.path, POWER)
        )
        """Utility incomer meters, on the HV side of their transformers."""
        bus_nodes = dict.fromkeys(d for i in self.incomers for d in design.downstream(i, POWER))
        self.gensets = of_type(GENSET_TYPE)
        self.buses: tuple[Bus, ...] = tuple(
            Bus(
                b,
                tuple(n for n in design.upstream(b, POWER) if n in self.incomers),
                tuple(n for n in design.upstream(b, POWER) if type_of(n) == GENSET_TYPE),
            )
            for b in bus_nodes
        )

        order: list[str] = []
        seen: set[str] = set()

        def visit(meter: str) -> None:
            if meter in seen:
                return
            seen.add(meter)
            for child in design.downstream(meter, POWER):
                if type_of(child) in METER_TYPES:
                    visit(child)
            order.append(meter)

        for bus in self.buses:
            visit(bus.node)
        self.order: tuple[str, ...] = tuple(order)
        """Every meter below the incomers, each after everything it feeds."""
        self.meters = frozenset(self.order)
        self.single_phase = frozenset(m for m in self.order if type_of(m) in SINGLE_PHASE_TYPES)
        self.boards: dict[str, Board] = {}
        for m in self.order:
            up = [n for n in design.upstream(m, POWER) if n in self.meters]
            if len(up) == 2:
                self.boards[m] = Board(m, up[0], up[1])
            elif len(up) > 2:
                raise ValueError(f"{m} is fed from {len(up)} boards")

        self.feeder: dict[str, str] = {}
        """The one node feeding each singly fed node."""
        feeds = []
        for m in self.order:
            loads, metered, boards = [], [], []
            children = design.downstream(m, POWER)
            if not children and m in bus_nodes:
                raise ValueError(f"main bus {m} feeds nothing")
            for c in children:
                if c in self.boards:
                    boards.append(c)
                    continue
                self.feeder[c] = m
                if c in self.meters or type_of(c) == UPS_TYPE:
                    metered.append(c)
                else:
                    loads.append((c, load_profile(design, c).tan_phi))
            feeds.append(Feed(m, tuple(loads), tuple(metered), tuple(boards), not children))
        self.feeds: tuple[Feed, ...] = tuple(feeds)

        self.ups = of_type(UPS_TYPE)
        self.branch_meters = of_type(BRANCH_TYPE)
        self.ups_branch: dict[str, str | None] = {}
        for u in self.ups:
            below = design.downstream(u, POWER)
            self.ups_branch[u] = below[0] if below else None
            for b in below:
                self.feeder[b] = u
        self.it: dict[str, tuple[str, ...]] = {
            n: design.upstream(n, POWER)
            for b in self.branch_meters
            for n in design.downstream(b, POWER)
        }
        """IT equipment → the UPS branches it draws from."""
        self.tanks: dict[str, tuple[str, ...]] = {
            t: design.downstream(t, ConnectionKind.FUEL) for t in of_type(TANK_TYPE)
        }
        self.ups_rooms: dict[str, tuple[str, ...]] = {}
        for u in self.ups:
            room = assets[u].room
            self.ups_rooms[room] = (*self.ups_rooms.get(room, ()), u)

        self.supply_flag: dict[str, str] = {
            **self.feeder,
            **{n: n for n in (*self.order, *self.incomers, *self.ups, *self.branch_meters)},
        }
        """Each singly supplied node → the node whose `live` flag says it has supply."""
        self.root: dict[str, str] = {}
        """Each meter → the bus or transfer board at the top of its branch."""
        for m in reversed(self.order):
            if m in bus_nodes or m in self.boards:
                self.root[m] = m
            else:
                self.root[m] = self.root[self.feeder[m]]
        for u in self.ups:
            self.root[u] = self.root[self.feeder[u]]
        self.weights: dict[str, tuple[float, float, float]] = {
            m: (1.0, 0.0, 0.0) if m in self.single_phase else _phase_weights(m)
            for m in (*self.order, *self.incomers)
        }
        """How each meter's own loads (not its sub-meters) share out across the phases."""

    def supply(self, state: WorldState, node: str) -> AssetState:
        """The bus state behind a meter or UPS, through a transfer board's current side."""
        root = state.assets[self.root[node]]
        board = self.boards.get(self.root[node])
        if board is None:
            return root
        source = root["source"]
        return state.assets[source if source is not None else board.preferred]


def _phase_weights(path: str) -> tuple[float, float, float]:
    """A fixed, small imbalance per board: circuits are never shared out perfectly."""
    digest = hashlib.blake2b(f"phase|{path}".encode(), digest_size=4).digest()
    e1 = 0.08 * (digest[0] / 255.0 - 0.5)
    e2 = 0.08 * (digest[1] / 255.0 - 0.5)
    return ((1.0 + e1) / 3.0, (1.0 + e2) / 3.0, (1.0 - e1 - e2) / 3.0)


@functools.cache
def network(design: PlantDesign) -> Network:
    return Network(design)


def load_profile(design: PlantDesign, node: str) -> LoadProfile:
    """Power factor and current distortion of a load, by what it is."""
    if design.is_room(node):
        return ROOM_PROFILE
    return PROFILES.get(design.asset(node).type_id, STAND_IN_PROFILE)


def powered(state: WorldState, design: PlantDesign, node: str) -> bool:
    """Whether `node` has a live supply: its own breaker for a meter, any branch for IT
    equipment, and otherwise whatever feeds it. True for nodes outside the power graph, and
    in a world that does not simulate the electrical network."""
    net = network(design)
    branches = net.it.get(node)
    if branches is not None:
        return any(_live(state, b) for b in branches)
    flag = net.supply_flag.get(node)
    return True if flag is None else _live(state, flag)


def _live(state: WorldState, node: str) -> bool:
    s = state.assets.get(node)
    return True if s is None else s.get("live", True)


def _tripped(s: AssetState) -> bool:
    return s.get("constraint.breaker_trip", 0.0) >= 0.5


def ups_rating(net: Network, ups: str) -> tuple[float, float, float, float]:
    """(kW, kVA, no-load loss kW, battery kWh at full health) of a UPS."""
    if net.ups_branch[ups] is None:
        kw, kva, no_load, minutes = (
            CONTROL_UPS_KW,
            CONTROL_UPS_KVA,
            CONTROL_UPS_NO_LOAD_KW,
            CONTROL_BATTERY_MIN,
        )
    else:
        kw, kva, no_load, minutes = UPS_KW, UPS_KVA, UPS_NO_LOAD_KW, BATTERY_MIN
    return kw, kva, no_load, kw * minutes / 60.0


def ups_load_pct(net: Network, ups: str, output_kw: float) -> float:
    kw, kva, _, _ = ups_rating(net, ups)
    return 100.0 * max(output_kw / kw, output_kw * math.hypot(1.0, IT_TAN) / kva)


def grid(ctx: StepContext) -> tuple[float, float]:
    """(voltage per unit, frequency) of the utility supply."""
    v = 1.0 + 0.008 * ctx.noise.smooth("grid/v", ctx.time, 900)
    hz = HZ + 0.02 * ctx.noise.smooth("grid/hz", ctx.time, 120)
    return v, hz


class ElectricalDomain:
    """Power flow, the UPS, gensets and fuel, and the ATS Controllers. It must step after
    every domain that reports a load's `power_kw`, and before the site totals."""

    settling_s = 3600
    """A fully discharged battery recharges within the hour."""

    def initial(self, ctx: StepContext) -> dict[str, AssetState]:
        net = network(ctx.design)
        v, hz = grid(ctx)
        states: dict[str, AssetState] = {}
        for m in (*net.order, *net.incomers):
            states[m] = {"p_kw": 0.0, "q_kvar": 0.0, "energy_kwh": 0.0, "live": True}
        for i in net.incomers:
            states[i].update(power_kw=0.0, q_loss_kvar=0.0, v_pu=v, hz=hz)
        for bus in net.buses:
            states[bus.node].update(
                source="utility",
                fed_by="utility",
                ats="utility",
                outage_s=0,
                restore_s=0,
                v_pu=v,
                hz=hz,
            )
        for board in net.boards.values():
            states[board.node].update(source=board.preferred, fed_by=board.preferred, timer_s=0)
        for b in net.branch_meters:
            states[b] = {"p_kw": 0.0, "q_kvar": 0.0, "energy_kwh": 0.0, "live": True}
        for u in net.ups:
            states[u] = {
                "mode": "online",
                "soc": 1.0,
                "p_kw": 0.0,
                "q_kvar": 0.0,
                "output_kw": 0.0,
                "loss_kw": 0.0,
                "battery_kw": 0.0,
                "power_kw": 0.0,
                "load_pct": 0.0,
                "live": True,
                "alarm_rectifier": False,
                "alarm_supply": False,
                "alarm_input": False,
                "alarm_bypass": False,
                "alarm_output": False,
                "has_alarm": False,
            }
        for g in net.gensets:
            states[g] = {
                "stage": "standby",
                "timer_s": 0,
                "start_cmd": False,
                "ready": False,
                "online": False,
                "speed_rpm": 0.0,
                "hz": 0.0,
                "v_pu": 0.0,
                "p_kw": 0.0,
                "q_kvar": 0.0,
                "p_prev_kw": 0.0,
                "load_pct": 0.0,
                "run_s": 0.0,
                "coolant_c": JACKET_C,
                "battery_v": 27.2,
                "day_l": DAY_STOP * DAY_TANK_L,
                "overcrank": False,
                "fuel_shutdown": False,
                "overload": False,
                "prealarm": False,
                "general_alarm": False,
                "has_alarm": False,
            }
        for t in net.tanks:
            states[t] = {
                "fuel_l": BULK_INITIAL_L,
                "fuel_pumped_l": 0.0,
                "pump_on": False,
                "flow_lpm": 0.0,
                "power_kw": PLC_KW,
                "pump_failed": False,
                "low_day_tank": False,
                "has_alarm": False,
            }
        for room in net.ups_rooms:
            states[room] = {"ups_heat_kw": 0.0}
        for a in ctx.design.assets.values():
            if a.type_id == "IPS" and a.room:
                states[a.path] = {"insulation_kohm": 500.0}
        return states

    def complete(self, state: WorldState, ctx: StepContext) -> None:
        self._flow(state, ctx, integrate=False)
        self._switch(state, ctx)

    def handles(self, event: Event, design: PlantDesign) -> bool:
        return False

    def apply(self, event: Event, state: WorldState) -> None:
        raise AssertionError("no events")

    def step(self, state: WorldState, ctx: StepContext) -> None:
        self._flow(state, ctx)
        self._equipment(state, ctx)
        self._switch(state, ctx)

    # ---- 1. Flow

    def _flow(self, state: WorldState, ctx: StepContext, integrate: bool = True) -> None:
        net = network(ctx.design)
        a = state.assets
        hours = ctx.dt / 3600.0 if integrate else 0.0

        for tank in net.tanks:
            s = a[tank]
            on = a[net.feeder[tank]]["live"]
            s["power_kw"] = (PLC_KW + (PUMP_KW if s["pump_on"] else 0.0)) if on else 0.0

        for board in net.boards:
            a[board]["fed_by"] = a[board]["source"]

        for it, branches in net.it.items():
            load = a[it]["power_kw"]
            live = [b for b in branches if a[b]["live"]]
            share = load / len(live) if live else 0.0
            for b in branches:
                s = a[b]
                p = share if s["live"] else 0.0
                s["p_kw"], s["q_kvar"] = p, p * IT_TAN
                s["energy_kwh"] += p * hours

        for u in net.ups:
            self._ups_flow(net, a[u], u, a, hours)
        for room, modules in net.ups_rooms.items():
            a[room]["ups_heat_kw"] = sum(a[u]["loss_kw"] for u in modules)

        for feed in net.feeds:
            p = q = 0.0
            for node, tan in feed.loads:
                x = a[node]["power_kw"]
                p += x
                q += x * tan
            if feed.stand_in:
                x = a[feed.meter]["power_kw"]
                p += x
                q += x * STAND_IN_PROFILE.tan_phi
            for node in feed.metered:
                s = a[node]
                p += s["p_kw"]
                q += s["q_kvar"]
            for board in feed.boards:
                s = a[board]
                if s["fed_by"] == feed.meter:
                    p += s["p_kw"]
                    q += s["q_kvar"]
            s = a[feed.meter]
            s["p_kw"], s["q_kvar"] = p, q
            s["energy_kwh"] += p * hours

        for bus in net.buses:
            s = a[bus.node]
            p, q = s["p_kw"], s["q_kvar"]
            s["fed_by"] = s["source"]
            supplying = (
                [i for i in bus.incomers if a[i]["live"]] if s["source"] == "utility" else []
            )
            for i in bus.incomers:
                si = a[i]
                if si["live"]:
                    share = 1.0 / len(supplying) if i in supplying else 0.0
                    sp, sq = p * share, q * share
                    kva = math.hypot(sp, sq)
                    loss = TX_NO_LOAD_KW + TX_FULL_LOAD_KW * (kva / TX_KVA) ** 2
                    q_loss = TX_MAGNETISING_KVAR + TX_REACTANCE * kva * kva / TX_KVA
                    si["p_kw"], si["q_kvar"] = sp + loss, sq + q_loss
                    si["power_kw"], si["q_loss_kvar"] = loss, q_loss
                else:
                    si["p_kw"] = si["q_kvar"] = si["power_kw"] = si["q_loss_kvar"] = 0.0
                si["energy_kwh"] += si["p_kw"] * hours
            online = [g for g in bus.gensets if a[g]["online"]] if s["source"] == "genset" else []
            for g in bus.gensets:
                sg = a[g]
                share = 1.0 / len(online) if g in online else 0.0
                sg["p_kw"], sg["q_kvar"] = p * share, q * share
                sg["load_pct"] = 100.0 * sg["p_kw"] / GENSET_KW

    def _ups_flow(self, net: Network, s: AssetState, ups: str, a, hours: float) -> None:
        kw, _, no_load, capacity = ups_rating(net, ups)
        branch = net.ups_branch[ups]
        mode = s["mode"]
        if branch is not None:
            out = a[branch]["p_kw"]
        else:
            out = CONTROL_LOAD_KW if mode != "off" else 0.0
        capacity *= 1.0 - s.get("constraint.battery_fade", 0.0)
        soc = s["soc"]
        charge = 0.0
        if mode in ("online", "bypass") and soc < 1.0:
            charge = CHARGE_C * capacity * min(1.0, (1.0 - soc) / (1.0 - TAPER_SOC))
        if mode == "online":
            loss = no_load + UPS_LOSS * out
            inp, battery = out + loss + charge, charge
            q = inp * UPS_INPUT_TAN
        elif mode == "bypass":
            loss = BYPASS_LOSS * out
            inp, battery = out + loss + charge, charge
            q = out * IT_TAN + charge * UPS_INPUT_TAN
        elif mode == "battery":
            loss = no_load + UPS_LOSS * out
            inp, battery, q = 0.0, -(out + loss), 0.0
        else:
            out = loss = inp = battery = q = 0.0
        s["p_kw"], s["q_kvar"] = inp, q
        s["output_kw"], s["loss_kw"], s["battery_kw"] = out, loss, battery
        s["power_kw"] = inp - out if branch is not None else inp
        s["load_pct"] = ups_load_pct(net, ups, out)
        if battery > 0.0:
            soc += battery * CHARGE_EFFICIENCY * hours / capacity
            s["soc"] = 1.0 if soc >= 1.0 - 1e-4 else soc
        elif battery < 0.0:
            s["soc"] = max(soc + battery * hours / capacity, 0.0)

    # ---- 2. Equipment

    def _equipment(self, state: WorldState, ctx: StepContext) -> None:
        net = network(ctx.design)
        a = state.assets
        for g in net.gensets:
            _engine(a[g], ctx.dt)
        for tank, gensets in net.tanks.items():
            s = a[tank]
            failed = s.get("constraint.pump_failure", 0.0) >= 0.5
            levels = [a[g]["day_l"] for g in gensets]
            threshold = DAY_STOP if s["pump_on"] else DAY_START
            wanted = any(level < threshold * DAY_TANK_L for level in levels)
            on = wanted and not failed and s["fuel_l"] > 0.0 and s["power_kw"] > 0.0
            delivered = 0.0
            if on:
                needing = [g for g in gensets if a[g]["day_l"] < DAY_STOP * DAY_TANK_L]
                each = min(PUMP_LPM * ctx.dt / 60.0, s["fuel_l"]) / max(len(needing), 1)
                for g in needing:
                    sg = a[g]
                    added = min(each, DAY_TANK_L - sg["day_l"])
                    sg["day_l"] += added
                    delivered += added
            s["fuel_l"] -= delivered
            s["fuel_pumped_l"] += delivered
            s["flow_lpm"] = delivered * 60.0 / ctx.dt
            s["pump_on"], s["pump_failed"] = on, failed
            s["low_day_tank"] = any(a[g]["day_l"] < 0.25 * DAY_TANK_L for g in gensets)
            s["has_alarm"] = failed or s["low_day_tank"] or s["fuel_l"] < 0.2 * BULK_TANK_L

    # ---- 3. Switching

    def _switch(self, state: WorldState, ctx: StepContext) -> None:
        design = ctx.design
        net = network(design)
        a = state.assets
        v_grid, hz_grid = grid(ctx)

        for i in net.incomers:
            s = a[i]
            loss = s.get("constraint.utility_loss", 0.0)
            s["live"] = loss < 0.5 and not _tripped(s)
            s["v_pu"] = v_grid * (1.0 - 0.4 * loss) if s["live"] else 0.0
            s["hz"] = hz_grid if s["live"] else 0.0

        for g in net.gensets:
            _genset_sequence(a[g])

        for bus in net.buses:
            self._ats(bus, a, ctx)

        for board in net.boards.values():
            s = a[board.node]
            current = s["source"]
            other = board.alternate if current == board.preferred else board.preferred
            if current == board.preferred:
                s["timer_s"] = s["timer_s"] + 1 if not a[current]["live"] else 0
                if s["timer_s"] >= DB_TRANSFER_S and a[other]["live"]:
                    current, s["timer_s"] = other, 0
            else:
                s["timer_s"] = s["timer_s"] + 1 if a[other]["live"] else 0
                if s["timer_s"] >= DB_RETRANSFER_S or (
                    not a[current]["live"] and s["timer_s"] >= DB_TRANSFER_S
                ):
                    current, s["timer_s"] = other, 0
            s["source"] = current
            s["live"] = a[current]["live"] and not _tripped(s)

        for m in reversed(net.order):
            if m in net.root and net.root[m] == m:
                continue  # a bus or transfer board, switched above
            s = a[m]
            s["live"] = a[net.feeder[m]]["live"] and not _tripped(s)

        for u in net.ups:
            self._ups_mode(net, a, u, state)

        for b in net.branch_meters:
            s = a[b]
            s["live"] = a[net.feeder[b]]["live"] and not _tripped(s)

    def _ats(self, bus: Bus, a, ctx: StepContext) -> None:
        """The ATS Controller of one main bus."""
        s = a[bus.node]
        utility = [i for i in bus.incomers if a[i]["live"]]
        stuck = s.get("constraint.ats_stuck", 0.0) >= 0.5
        s["outage_s"] = 0 if utility else s["outage_s"] + 1
        position = s["ats"]
        ready = [g for g in bus.gensets if a[g]["ready"]]
        if not stuck:
            if position == "utility" and not utility:
                if len(ready) >= len(bus.gensets) - 1:  # N of N+1 are up to speed
                    position = "genset"
            elif position == "genset":
                s["restore_s"] = s["restore_s"] + 1 if utility else 0
                if s["restore_s"] >= RETRANSFER_S:
                    position = "utility"
        if position == "utility":
            s["restore_s"] = 0
        s["ats"] = position
        start = s["outage_s"] >= CONFIRM_S or position == "genset"
        tripped = _tripped(s)
        online = []
        for g in bus.gensets:
            sg = a[g]
            sg["start_cmd"] = start
            sg["online"] = position == "genset" and sg["ready"] and not tripped
            if sg["online"]:
                online.append(sg)
        source = None
        if not tripped:
            if position == "utility" and utility:
                source = "utility"
            elif position == "genset" and online:
                source = "genset"
        s["source"], s["live"] = source, source is not None
        if source == "utility":
            kva = math.hypot(s["p_kw"], s["q_kvar"]) / len(utility)
            v = sum(a[i]["v_pu"] for i in utility) / len(utility)
            s["v_pu"] = v * (1.0 + TX_TAP) - TX_DROP * kva / TX_KVA
            s["hz"] = a[utility[0]]["hz"]
        elif source == "genset":
            s["v_pu"] = sum(g["v_pu"] for g in online) / len(online)
            s["hz"] = sum(g["hz"] for g in online) / len(online)
        else:
            s["v_pu"] = s["hz"] = 0.0

    def _ups_mode(self, net: Network, a, ups: str, state: WorldState) -> None:
        s = a[ups]
        feed = a[net.feeder[ups]]
        supply = net.supply(state, ups)
        v, hz = supply["v_pu"], supply["hz"]
        input_live = feed["live"]
        rectifier_ok = s.get("constraint.rectifier_failure", 0.0) < 0.5
        pct = s["load_pct"]
        overloaded = pct > OVERLOAD_PCT or (s["mode"] == "bypass" and pct > BYPASS_RETURN_PCT)
        if input_live and overloaded:
            mode = "bypass"
        elif input_live and rectifier_ok:
            mode = "online"
        elif s["soc"] > 0.0 and s["mode"] != "off":
            mode = "battery"
        else:
            mode = "off"
        s["mode"], s["live"] = mode, mode != "off"
        s["alarm_rectifier"] = not rectifier_ok
        s["alarm_supply"] = not input_live
        s["alarm_input"] = not input_live or v < 0.9 or abs(hz - HZ) > 2.0
        s["alarm_bypass"] = not input_live or v < 0.9
        s["alarm_output"] = mode == "off" or pct > 100.0
        s["has_alarm"] = (
            s["alarm_rectifier"]
            or s["alarm_supply"]
            or s["alarm_input"]
            or s["alarm_bypass"]
            or s["alarm_output"]
        )


def _genset_sequence(s: AssetState) -> None:
    """The genset's own start/stop sequence, following the ATS Controller's start command."""
    stage, start = s["stage"], s["start_cmd"]
    fail = s.get("constraint.fail_to_start", 0.0) >= 0.5
    if stage == "standby":
        if start:
            stage, s["timer_s"] = "cranking", 0
    elif stage == "cranking":
        s["timer_s"] += 1
        if not start:
            stage = "standby"
        elif fail:
            if s["timer_s"] >= OVERCRANK_S:
                stage, s["overcrank"] = "failed", True
        elif s["timer_s"] >= CRANK_S:
            stage, s["timer_s"] = "running", 0
    elif stage == "running":
        if not start:
            stage, s["timer_s"] = "cooldown", 0
    elif stage == "cooldown":
        s["timer_s"] += 1
        if start:
            stage = "running"
        elif s["timer_s"] >= COOLDOWN_S:
            stage = "standby"
    elif stage == "failed":
        if s["overcrank"] and not fail:
            stage, s["overcrank"] = "standby", False  # the fault is cleared: reset
        elif s["fuel_shutdown"] and s["day_l"] >= DAY_START * DAY_TANK_L:
            stage, s["fuel_shutdown"] = "standby", False
    if stage in ("running", "cooldown") and s["day_l"] <= 0.0:
        stage, s["fuel_shutdown"] = "failed", True
    s["stage"] = stage
    running = stage in ("running", "cooldown")
    if stage == "cranking":
        s["speed_rpm"] = CRANK_RPM
    elif running and s["speed_rpm"] < 0.97 * RATED_RPM and not s["ready"]:
        s["speed_rpm"] = min(RATED_RPM, s["speed_rpm"] + (RATED_RPM - CRANK_RPM) / RUN_UP_S)
        s["v_pu"] = s["speed_rpm"] / RATED_RPM
    elif not running:
        s["speed_rpm"] = s["v_pu"] = 0.0
    s["hz"] = s["speed_rpm"] / 30.0 if running else 0.0
    # Once up to speed it stays ready through the dips of taking load, until it stops.
    s["ready"] = stage == "running" and (
        s["ready"] or (s["speed_rpm"] >= 0.97 * RATED_RPM and s["v_pu"] >= 0.95)
    )


def _engine(s: AssetState, dt: float) -> None:
    """Speed and voltage under load, the engine's temperatures and battery, and its fuel."""
    running = s["stage"] in ("running", "cooldown")
    frac = s["p_kw"] / GENSET_KW
    if running and (s["ready"] or s["speed_rpm"] >= 0.97 * RATED_RPM):
        step = (s["p_kw"] - s["p_prev_kw"]) / GENSET_KW
        target = RATED_RPM * (1.0 + DROOP_HZ * (0.5 - frac))
        v_target = 1.0 + DROOP_V * (0.5 - frac)
        s["speed_rpm"] += 0.5 * (target - s["speed_rpm"]) - DIP_RPM_PER_PU * step
        s["v_pu"] += 0.7 * (v_target - s["v_pu"]) - DIP_V_PER_PU * step
        s["hz"] = s["speed_rpm"] / 30.0
    s["p_prev_kw"] = s["p_kw"]
    target_c = 75.0 + 15.0 * frac if running else JACKET_C
    s["coolant_c"] += (target_c - s["coolant_c"]) * dt / COOLANT_TAU_S
    s["battery_v"] = {"cranking": 21.5, "running": 28.2, "cooldown": 28.2}.get(s["stage"], 27.2)
    if running:
        s["run_s"] += dt
        burn = GENSET_KW * (0.03 + 0.22 * frac) * dt / 3600.0
        s["day_l"] = max(s["day_l"] - burn, 0.0)
    s["overload"] = s["load_pct"] > 100.0
    s["prealarm"] = s["overload"] or s["coolant_c"] > 95.0 or s["day_l"] < 0.25 * DAY_TANK_L
    s["general_alarm"] = s["overcrank"] or s["fuel_shutdown"]
    s["has_alarm"] = s["general_alarm"] or s["prealarm"]


# ---- Plant Design lookups the site totals share


@functools.cache
def hall_ups(design: PlantDesign) -> dict[str, tuple[str, ...]]:
    """Data Hall → the UPS modules feeding its IT equipment."""
    net = network(design)
    modules: dict[str, tuple[str, ...]] = {}
    for it, branches in net.it.items():
        hall = design.asset(it).room
        ups = tuple(net.feeder[b] for b in branches)
        modules[hall] = tuple(dict.fromkeys([*modules.get(hall, ()), *ups]))
    return modules


@functools.cache
def control_ups(design: PlantDesign) -> tuple[str, ...]:
    net = network(design)
    return tuple(u for u in net.ups if net.ups_branch[u] is None)


def incomers(design: PlantDesign) -> tuple[str, ...]:
    return network(design).incomers
