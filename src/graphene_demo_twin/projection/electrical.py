"""Point bindings for the electrical network (#19): every meter's readings, the UPS, the branch
circuit monitors, gensets, diesel tanks and the control room's IPS and RCMS circuits.

The flow in `sim.electrical` keeps each meter's total P and Q. Its per-phase figures come from
one pass here: the meter's own loads share out across the phases by its board's fixed weights
and everything below it adds phase by phase, so per phase and in total a board is the sum of
its sub-meters (a single-phase board sits on phase 1 of the board above it). Voltage falls
from the bus down each feeder with the current it carries, and current distortion adds up
from the loads. Every reading takes the switching the step's power flowed through (`fed`,
`fed_by`, `fed_v_pu`, …), not the switching the step left for the next one, so V, I, P, Q, S
and PF always describe the same interval.
"""

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from graphene_demo_twin.asset_model import AssetModel
from graphene_demo_twin.plant_design import PlantDesign
from graphene_demo_twin.projection.projector import Binding, GroupBinding, VariableRead
from graphene_demo_twin.sim import Scalar, WorldState
from graphene_demo_twin.sim.electrical import (
    BULK_TANK_L,
    GENSET_KVA,
    HV_LL,
    HZ,
    IT_BRANCH_THD,
    RATED_RPM,
    STAND_IN_PROFILE,
    TX_KVA,
    V_LL,
    V_LN,
    load_profile,
    network,
)

PHASE_V = (0.002, -0.003, 0.001)
"""Fixed voltage unbalance of the supply, per phase."""
THD_SHARE = ((1.0, 1.0, 1.0), (1.0, 1.04, 0.97), (1.0, 0.97, 1.03))
"""Per-phase spread of (unused, current, voltage) distortion around the meter's figure."""
DROP_PER_KA = 0.008
"""Feeder voltage drop per kA of current, per unit."""
UTILITY_THDV, GENSET_THDV = 1.1, 2.8
"""Background voltage distortion of each kind of source, in %."""
SOURCE_IMPEDANCE = {"utility": 0.25, "genset": 0.6}
"""Voltage distortion per % of harmonic current relative to the source rating."""
UPS_THD = 3.0
RCMS_PF = 0.9


@dataclass(slots=True)
class Reading:
    """What one meter sees at one instant."""

    p: list[float]
    q: list[float]
    harmonic_kva: float
    """Sum of distortion (%) × apparent power over everything it feeds."""
    v: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    """Phase-to-neutral volts."""
    hz: float = 0.0
    thd_v: float = 0.0

    @property
    def s(self) -> list[float]:
        return [math.hypot(p, q) for p, q in zip(self.p, self.q, strict=True)]

    @property
    def thd_i(self) -> float:
        total = sum(self.s)
        return self.harmonic_kva / total if total > 0.0 else 0.0


def readings(state: WorldState, design: PlantDesign) -> dict[str, Reading]:
    """Every meter's per-phase reading (the incomers' too), from one pass over the network."""
    net = network(design)
    a = state.assets
    out: dict[str, Reading] = {}

    for feed in net.feeds:
        m = feed.meter
        s = a[m]
        p3, q3 = [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]
        harmonic = sub_p = sub_q = 0.0
        below = [c for c in feed.metered if c in net.meters]
        below += [b for b in feed.boards if a[b]["fed_by"] == m]
        for c in below:
            r, cs = out[c], a[c]
            sub_p += cs["p_kw"]
            sub_q += cs["q_kvar"]
            if c in net.single_phase:
                p3[0] += cs["p_kw"]
                q3[0] += cs["q_kvar"]
            else:
                for k in range(3):
                    p3[k] += r.p[k]
                    q3[k] += r.q[k]
            harmonic += r.harmonic_kva
        own_p, own_q = s["p_kw"] - sub_p, s["q_kvar"] - sub_q
        w = net.weights[m]
        for k in range(3):
            p3[k] += own_p * w[k]
            q3[k] += own_q * w[k]
        for node, _ in feed.loads:
            profile = load_profile(design, node)
            kw = a[node]["power_kw"]
            harmonic += profile.thd_pct * kw * math.hypot(1.0, profile.tan_phi)
        if feed.stand_in:
            kw = s["power_kw"]
            harmonic += STAND_IN_PROFILE.thd_pct * kw * math.hypot(1.0, STAND_IN_PROFILE.tan_phi)
        for u in feed.metered:
            if u not in net.meters:
                us = a[u]
                thd = IT_BRANCH_THD if us["mode"] == "bypass" else UPS_THD
                harmonic += thd * math.hypot(us["p_kw"], us["q_kvar"])
        if m in net.single_phase:
            p3, q3 = [s["p_kw"], 0.0, 0.0], [s["q_kvar"], 0.0, 0.0]
        out[m] = Reading(p3, q3, harmonic)

    for bus in net.buses:
        s, r = a[bus.node], out[bus.node]
        source = s["fed_by"]
        if s["fed"] and source is not None:
            r.v = [V_LN * s["fed_v_pu"] * (1.0 + d) for d in PHASE_V]
            r.hz = s["fed_hz"]
            rating = s["fed_units"] * (TX_KVA if source == "utility" else GENSET_KVA)
            base = UTILITY_THDV if source == "utility" else GENSET_THDV
            r.thd_v = base + SOURCE_IMPEDANCE[source] * r.harmonic_kva / max(rating, 1.0)
        for i in bus.incomers:
            si = a[i]
            share = (si["p_kw"] - si["power_kw"]) / s["p_kw"] if s["p_kw"] else 0.0
            ri = Reading(
                [r.p[k] * share + si["power_kw"] / 3.0 for k in range(3)],
                [r.q[k] * share + si["q_loss_kvar"] / 3.0 for k in range(3)],
                r.harmonic_kva * share,
            )
            if si["fed"]:
                ri.v = [HV_LL / math.sqrt(3.0) * si["fed_v_pu"] * (1.0 + d) for d in PHASE_V]
                ri.hz = si["fed_hz"]
                ri.thd_v = UTILITY_THDV
            out[i] = ri

    for m in reversed(net.order):
        if net.root[m] == m and m not in net.boards:
            continue  # a bus, done above
        s, r = a[m], out[m]
        feeder = s["fed_by"] if m in net.boards else net.feeder[m]
        parent = out[feeder]
        if not s["fed"] or parent.v[0] <= 0.0:
            continue
        pv = parent.v
        s3 = r.s
        drop = 0.0
        if m in net.single_phase:
            drop = DROP_PER_KA * s3[0] / pv[0]
            r.v = [pv[0] * (1.0 - drop), 0.0, 0.0]
        else:
            drops = [DROP_PER_KA * s3[k] / pv[k] for k in range(3)]
            r.v = [pv[k] * (1.0 - drops[k]) for k in range(3)]
            drop = max(drops)
        r.hz = parent.hz
        r.thd_v = parent.thd_v * (1.0 + 2.0 * drop)
    return out


# ---- Point values


def _line(v: list[float], i: int, j: int) -> float:
    """Line-to-line volts from two phase-to-neutral voltages 120° apart."""
    return math.sqrt(v[i] ** 2 + v[j] ** 2 + v[i] * v[j])


def _meter_values(r: Reading, s: dict[str, Scalar], three: bool) -> dict[str, Scalar]:
    phases = range(3) if three else range(1)
    s3 = r.s
    amps = [s3[k] * 1000.0 / r.v[k] if r.v[k] > 0.0 else 0.0 for k in range(3)]
    values: dict[str, Scalar] = {"Hz": r.hz, "Wh_Im": s["energy_kwh"]}
    for k in phases:
        n = k + 1
        values[f"V{n}"] = r.v[k]
        values[f"I{n}"] = amps[k]
        values[f"P{n}"] = r.p[k]
        values[f"Q{n}"] = r.q[k]
        values[f"S{n}"] = s3[k]
        values[f"PF{n}"] = r.p[k] / s3[k] if s3[k] > 0.0 else 0.0
        values[f"THDA{n}"] = r.thd_i * THD_SHARE[1][k]
        values[f"THDV{n}"] = r.thd_v * THD_SHARE[2][k]
    ptot, qtot, stot = sum(r.p[k] for k in phases), sum(r.q[k] for k in phases), 0.0
    for k in phases:
        stot += s3[k]
    values.update(Ptot=ptot, Qtot=qtot, Stot=stot, PFsys=ptot / stot if stot > 0.0 else 0.0)
    if three:
        re = im = 0.0
        for k in range(3):
            angle = -2.0 * math.pi * k / 3.0 - math.atan2(r.q[k], r.p[k])
            re += amps[k] * math.cos(angle)
            im += amps[k] * math.sin(angle)
        triplen = 3.0 * sum(amps) / 3.0 * r.thd_i / 100.0 * 0.3
        values["In"] = math.hypot(math.hypot(re, im), triplen)
        values["Isys"] = sum(amps) / 3.0
        values["V12"], values["V23"], values["V31"] = (
            _line(r.v, 0, 1),
            _line(r.v, 1, 2),
            _line(r.v, 2, 0),
        )
        values["Vsys"] = (values["V12"] + values["V23"] + values["V31"]) / 3.0
    else:
        values["In"] = values["Isys"] = amps[0]
        values["Vsys"] = r.v[0]
    return values


def _ups_values(net, a, r_feed: Reading, ups: str) -> dict[str, Scalar]:
    s = a[ups]
    mode = s["fed_mode"]
    feed = a[net.feeder[ups]]
    v_in = [_line(r_feed.v, 0, 1), _line(r_feed.v, 1, 2), _line(r_feed.v, 2, 0)]
    if not feed["fed"]:
        v_in = [0.0, 0.0, 0.0]
    if mode in ("online", "battery"):
        v_out = [V_LL * (1.0 + d) for d in (0.0005, 0.001, -0.0005)]
    elif mode == "bypass":
        v_out = list(v_in)
    else:
        v_out = [0.0, 0.0, 0.0]
    hz_in = r_feed.hz if feed["fed"] else 0.0
    hz = {"online": hz_in if hz_in else HZ, "battery": HZ, "bypass": hz_in}.get(mode, 0.0)
    w = net.weights[net.feeder[ups]]
    values: dict[str, Scalar] = {
        "Input Voltage L1-L2": v_in[0],
        "Input Voltage L2-L3": v_in[1],
        "Input Voltage L3-L1": v_in[2],
        "Average Input Voltage": sum(v_in) / 3.0,
        "Output Voltage L1-L2": v_out[0],
        "Output Voltage L2-L3": v_out[1],
        "Output Voltage L3-L1": v_out[2],
        "Average Output Voltage": sum(v_out) / 3.0,
        "Frequency": hz,
    }
    for k in range(3):
        values[f"Input Power L{k + 1}"] = s["p_kw"] * w[k]
    return values


def _branch_values(a, ups_values: dict[str, Scalar], branch: str) -> dict[str, Scalar]:
    s = a[branch]
    volts = ups_values["Average Output Voltage"]
    kva = math.hypot(s["p_kw"], s["q_kvar"])
    amps = kva * 1000.0 / (math.sqrt(3.0) * volts) if volts > 0.0 else 0.0
    return {"Active Power": s["p_kw"], "Current": amps, "Accumulated Energy": s["energy_kwh"]}


def _genset_values(s: dict[str, Scalar]) -> dict[str, Scalar]:
    running = s["stage"] in ("running", "cooldown")
    volts = [V_LN * s["v_pu"] * (1.0 + d) if running else 0.0 for d in PHASE_V]
    return {
        "AC Voltage: L1-N": volts[0],
        "AC Voltage: L2-N": volts[1],
        "AC Voltage: L3-N": volts[2],
        "Frequency": s["hz"],
        "Engine Speed": s["speed_rpm"],
        "Coolant Temperature": s["coolant_c"],
        "Oil Pressure": _oil_bar(s),
        "Battery DC Volts": s["battery_v"],
        "Engine Run Time": s["run_s"] / 60.0,
        "Engine Start": 1.0 if s["stage"] == "cranking" else 0.0,
        "Run Command Active": 1 if s["start_cmd"] else 0,
        "Idling": 1 if running and s["p_kw"] == 0.0 else 0,
    }


def _oil_bar(s: dict[str, Scalar]) -> float:
    if s["stage"] not in ("running", "cooldown"):
        return 0.0
    return 1.0 + 3.5 * s["speed_rpm"] / RATED_RPM


GENSET_ALARMS: dict[str, Callable[[dict[str, Scalar]], Scalar]] = {
    "Over Crank Shutdown": lambda s: s["overcrank"],
    "General Genset Alarm": lambda s: s["general_alarm"],
    "Genset Prealarm": lambda s: s["prealarm"],
    "Overload Warning": lambda s: s["overload"],
    "Low Lubricant Oil Pressure Prealarm": lambda s: (
        s["stage"] in ("running", "cooldown") and _oil_bar(s) < 2.0
    ),
    "HasAlarm": lambda s: s["has_alarm"],
}
"""Genset alarm member → the engine controller's logic over its state."""

UPS_ALARMS: dict[str, str] = {
    "Rectifier Failure": "alarm_rectifier",
    "Power Supply Failure": "alarm_supply",
    "System Input Power Problem": "alarm_input",
    "Bypass Undervoltage Warning": "alarm_bypass",
    "System Output Fault": "alarm_output",
    "HasAlarm": "has_alarm",
}


def _panel_on(s: dict[str, Scalar]) -> bool:
    return s["power_kw"] > 0.0


TANK_POINTS: dict[str, Callable[[dict[str, Scalar]], Scalar]] = {
    "Flow Totalizer - Flowmeter A": lambda s: s["fuel_pumped_l"],
    "Flowmeter - Flowmeter A": lambda s: s["flow_lpm"],
    "Run_Stop - Fuel Pump A": lambda s: 1 if s["pump_on"] else 0,
    "On_Off - PLC Panel A": lambda s: 1 if _panel_on(s) else 0,
    "On_Off - PLC Panel B": lambda s: 1 if _panel_on(s) else 0,
    "Open_Close Feedback - Inlet Valve": lambda s: 1 if _panel_on(s) else 0,
    "System Failure_Trip - Fuel Pump A": lambda s: s["pump_failed"],
    "System Failure_Trip - PLC Panel A": lambda s: not _panel_on(s),
    "System Failure_Trip - PLC Panel B": lambda s: not _panel_on(s),
    "General Alarm - PLC Panel A": lambda s: s["pump_failed"] or s["fuel_l"] < 0.2 * BULK_TANK_L,
    "General Alarm - PLC Panel B": lambda s: s["low_day_tank"],
    "HasAlarm": lambda s: s["has_alarm"] or not _panel_on(s),
}
"""Diesel tank member → its value from the tank's AssetState (fuel pump, flowmeter, panels)."""

UNMODELLED: Mapping[str, tuple[str, ...]] = {
    "Genset": (
        "Auto_Manual",
        "Low Lubricant Oil Pressure Shutdown",
        "Short Circuit Shutdown",
        "Emergency Stop",
        "Low Coolant Level",
    ),
    "Diesel": ("Time-out Alarm - Inlet Valve",),
    "IPS": ("Insulation Fault",),
}
"""Compatibility Fallback debt, by asset type: the genset's control mode and protective
shutdowns, the diesel inlet valve's travel and the IPS insulation monitor have no physics yet.
Their points keep their fallback, which the coverage report counts as debt, rather than a
constant bound as if it were physics."""


METER_KEYS_3 = frozenset(
    {"Hz", "Wh_Im", "In", "Isys", "Vsys", "V12", "V23", "V31", "Ptot", "Qtot", "Stot", "PFsys"}
    | {f"{x}{k}" for x in ("V", "I", "P", "Q", "S", "PF", "THDA", "THDV") for k in (1, 2, 3)}
)
METER_KEYS_1 = frozenset(
    {"Hz", "Wh_Im", "In", "Isys", "Vsys", "Ptot", "Qtot", "Stot", "PFsys"}
    | {f"{x}1" for x in ("V", "I", "P", "Q", "S", "PF", "THDA", "THDV")}
)
UPS_KEYS = frozenset(
    {"Average Input Voltage", "Average Output Voltage", "Frequency"}
    | {f"Input Voltage {p}" for p in ("L1-L2", "L2-L3", "L3-L1")}
    | {f"Output Voltage {p}" for p in ("L1-L2", "L2-L3", "L3-L1")}
    | {f"Input Power L{k}" for k in (1, 2, 3)}
)
BRANCH_KEYS = frozenset({"Active Power", "Current", "Accumulated Energy"})
GENSET_KEYS = frozenset(
    {"Frequency", "Engine Speed", "Coolant Temperature", "Oil Pressure", "Battery DC Volts"}
    | {"Engine Run Time", "Engine Start", "Run Command Active", "Idling"}
    | {f"AC Voltage: L{k}-N" for k in (1, 2, 3)}
)

type Compute = Callable[[dict, dict[str, Reading]], dict[str, Scalar]]


def electrical_bindings(
    asset_model: AssetModel, design: PlantDesign
) -> list[Binding | GroupBinding]:
    net = network(design)
    entries: list[tuple[Compute, tuple[str, ...]]] = []
    paths: list[str] = []

    def group(node: str, keys: frozenset[str], compute: Compute) -> None:
        members = tuple(p.name for p in asset_model.points_of(node) if p.name in keys)
        if missing := keys - set(members):
            raise ValueError(f"{node} has no points {sorted(missing)}")
        entries.append((compute, members))
        paths.extend(f"{node}/{m}" for m in members)

    def branch_compute(b: str, u: str) -> Compute:
        return lambda a, r: _branch_values(a, _ups_values(net, a, r[net.feeder[u]], u), b)

    bindings: list[Binding | GroupBinding] = []
    for m in (*net.incomers, *net.order):
        three = m not in net.single_phase
        keys = METER_KEYS_3 if three else METER_KEYS_1
        group(m, keys, lambda a, r, m=m, t=three: _meter_values(r[m], a[m], t))
        bindings.append(Binding(f"{m}/HasAlarm", _meter_alarm(net, m)))
    for u in net.ups:
        group(u, UPS_KEYS, lambda a, r, u=u: _ups_values(net, a, r[net.feeder[u]], u))
        for member, var in UPS_ALARMS.items():
            bindings.append(Binding(f"{u}/{member}", _var(u, var)))
    for b in net.branch_meters:
        group(b, BRANCH_KEYS, branch_compute(b, net.feeder[b]))
    for g in net.gensets:
        group(g, GENSET_KEYS, lambda a, r, g=g: _genset_values(a[g]))
        for member, read in GENSET_ALARMS.items():
            bindings.append(Binding(f"{g}/{member}", _read(g, read)))
    for t in net.tanks:
        for member, read in TANK_POINTS.items():
            bindings.append(Binding(f"{t}/{member}", _read(t, read)))
    for placed in design.assets.values():
        if placed.type_id == "RCMS" and placed.room:
            bindings.append(Binding(f"{placed.path}/Current", _rcms_amps(placed.path)))

    frozen = tuple(entries)

    def read_all(state: WorldState) -> list[Scalar]:
        r = readings(state, design)
        a = state.assets
        values: list[Scalar] = []
        for compute, members in frozen:
            computed = compute(a, r)
            values.extend(computed[m] for m in members)
        return values

    bindings.append(GroupBinding(tuple(paths), read_all))
    named = [*paths, *(b.path for b in bindings if isinstance(b, Binding))]
    if missing := [p for p in named if p not in asset_model.points]:
        raise ValueError(f"electrical bindings name points not in the Asset Model: {missing}")
    return bindings


def _meter_alarm(net, meter: str) -> Callable[[WorldState], bool]:
    """The meter's own undervoltage alarm: its circuit is dead or its supply sags."""

    def read(state: WorldState) -> bool:
        s = state.assets[meter]
        if meter in net.incomers:
            return not s["live"] or s["v_pu"] < 0.9
        return not s["live"] or net.supply(state, meter)["v_pu"] < 0.9

    return read


def _rcms_amps(node: str) -> Callable[[WorldState], float]:
    """Load current of a residual-current-monitored circuit."""
    return lambda s: s.assets[node]["power_kw"] * 1000.0 / (math.sqrt(3.0) * V_LL * RCMS_PF)


def _var(node: str, name: str) -> Callable[[WorldState], Scalar]:
    return VariableRead(node, name)


def _read(node: str, read: Callable[[dict[str, Scalar]], Scalar]) -> Callable[[WorldState], Scalar]:
    return lambda s: read(s.assets[node])
