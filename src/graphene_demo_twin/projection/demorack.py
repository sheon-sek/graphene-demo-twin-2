"""Point bindings for the Demo Rack (#27): the E820's branch circuits, the 14 breakers and the
seven demo meters, all read from the rack's own AssetState (`sim.demorack`)."""

import math
import re
from collections.abc import Callable, Iterable

from graphene_demo_twin.asset_model import AssetModel
from graphene_demo_twin.plant_design import PlantDesign
from graphene_demo_twin.projection.projector import Binding, VariableRead
from graphene_demo_twin.sim import Scalar, WorldState
from graphene_demo_twin.sim.demorack import (
    BANKS,
    BREAKERS,
    INCOMER,
    METERS,
    SLOTS,
    circuit_pf,
    circuit_tan,
)

CIRCUIT_POINT = re.compile(r"^(B[ab]\d)_I(\d\d)_(Current|P|PF|Q|S|Wh_Im)$")
THDA_PCT, THDV_PCT = 7.0, 1.2
"""Current distortion of the rack's switch-mode loads and voltage distortion of its supply."""
_DRIFT = (1.0, 1.04, 0.97)


def demorack_bindings(asset_model: AssetModel, design: PlantDesign) -> list[Binding]:
    bindings = [*_breakers(), *_incomer(asset_model), *_meters(asset_model)]
    missing = [b.path for b in bindings if b.path not in asset_model.points]
    if missing:
        raise ValueError(f"Demo Rack bindings name points not in the Asset Model: {missing}")
    return bindings


def _breakers() -> Iterable[Binding]:
    for b in BREAKERS:
        yield Binding(f"{b}/OnOff", VariableRead(b, "closed"))
        yield Binding(f"{b}/Trip", VariableRead(b, "trip"))
        yield Binding(f"{b}/EF", VariableRead(b, "ef"))


def _line(a: float, b: float) -> float:
    return math.sqrt(a * a + b * b + a * b)


def _amps(p: float, q: float, v: float) -> float:
    return math.hypot(p, q) * 1000.0 / v if v > 0.0 else 0.0


def _incomer(asset_model: AssetModel) -> Iterable[Binding]:
    for point in asset_model.points.values():
        if not point.path.startswith(f"{INCOMER}/"):
            continue
        m = CIRCUIT_POINT.match(point.path.rsplit("/", 1)[1])
        if m:
            bank, slot, what = BANKS.index(m[1]), int(m[2]) - 1, m[3]
            yield Binding(point.path, _circuit(bank * SLOTS + slot, what))
    yield from _summary(INCOMER, asset_model, three=True)


def _circuit(k: int, what: str) -> Callable[[WorldState], Scalar]:
    phase = (k // SLOTS) % 3
    pf, tan = circuit_pf(k), circuit_tan(k)

    def read(state: WorldState) -> Scalar:
        inc = state.assets[INCOMER]
        kw = inc[f"c{k}_kw"]
        s = kw / pf
        match what:
            case "P":
                return kw
            case "Q":
                return kw * tan
            case "S":
                return s
            case "PF":
                return pf if kw > 0.0 else 0.0
            case "Current":
                return _amps(kw, kw * tan, inc[f"v{phase + 1}"])
            case _:
                return inc[f"c{k}_kwh"]

    return read


def _meters(asset_model: AssetModel) -> Iterable[Binding]:
    for node, meter in METERS.items():
        yield from _summary(node, asset_model, three=meter.phases == 3, dc=meter.dc)


def _summary(
    node: str, asset_model: AssetModel, *, three: bool, dc: bool = False
) -> Iterable[Binding]:
    """Every conventional meter member of `node` (a phase reading, a system total, or the
    energy), from its per-phase p/q/v state. Members a meter model does not export are
    skipped, so one table serves every model."""

    def phase(
        n: int, read: Callable[[float, float, float], float]
    ) -> Callable[[WorldState], Scalar]:
        return lambda st: read(*(st.assets[node][f"{c}{n}"] for c in "pqv"))

    phases = range(1, 4) if three else range(1, 2)
    members: dict[str, Callable[[WorldState], Scalar]] = {
        "HasAlarm": VariableRead(node, "has_alarm"),
        "Hz": VariableRead(node, "hz"),
    }
    members["Wh_Im"] = VariableRead(node, "energy_kwh")
    for n in phases:
        members[f"V{n}"] = VariableRead(node, f"v{n}")
        members[f"P{n}"] = VariableRead(node, f"p{n}")
        members[f"Q{n}"] = VariableRead(node, f"q{n}")
        members[f"S{n}"] = phase(n, lambda p, q, v: math.hypot(p, q))
        members[f"I{n}"] = phase(n, lambda p, q, v: _amps(p, q, v))
        members[f"PF{n}"] = phase(n, lambda p, q, v: 1.0 if dc and v > 0.0 else _pf(p, q))
        members[f"THDA{n}"] = phase(n, lambda p, q, v, n=n: _thd(THDA_PCT, n, p))
        members[f"THDV{n}"] = phase(n, lambda p, q, v, n=n: _thd(THDV_PCT, n, v))

    def total(index: int) -> Callable[[WorldState], Scalar]:
        return lambda st: sum(st.assets[node][f"{'pq'[index]}{n}"] for n in phases)

    members["Ptot"], members["Qtot"] = total(0), total(1)
    members["Stot"] = lambda st: sum(
        math.hypot(st.assets[node][f"p{n}"], st.assets[node][f"q{n}"]) for n in phases
    )
    members["PFsys"] = lambda st: _pf(members["Ptot"](st), members["Qtot"](st))

    def currents(st: WorldState) -> list[float]:
        a = st.assets[node]
        return [_amps(a[f"p{n}"], a[f"q{n}"], a[f"v{n}"]) for n in phases]

    members["Isys"] = lambda st: sum(currents(st)) / len(phases)
    members["Iasys"] = members["Isys"]
    members["Wh_Ima"] = members["Wh_Im"]

    def neutral(st: WorldState) -> float:
        i = currents(st) + [0.0, 0.0]
        return math.sqrt(
            max(i[0] ** 2 + i[1] ** 2 + i[2] ** 2 - i[0] * i[1] - i[1] * i[2] - i[2] * i[0], 0.0)
        )

    members["In"] = neutral if three else members["Isys"]
    if three:
        for name, (i, j) in {"V12": (1, 2), "V23": (2, 3), "V31": (3, 1)}.items():
            members[name] = lambda st, i=i, j=j: _line(
                st.assets[node][f"v{i}"], st.assets[node][f"v{j}"]
            )
        members["Vsys"] = lambda st: sum(members[n](st) for n in ("V12", "V23", "V31")) / 3.0
        members["Vsys2"] = lambda st: sum(st.assets[node][f"v{n}"] for n in phases) / 3.0
    else:
        members["Vsys"] = members["Vsys2"] = members["V1"]
    prefix = f"{node}/"
    for name, read in members.items():
        if prefix + name in asset_model.points:
            yield Binding(prefix + name, read)


def _pf(p: float, q: float) -> float:
    s = math.hypot(p, q)
    return p / s if s > 0.0 else 0.0


def _thd(pct: float, n: int, live: float) -> float:
    return pct * _DRIFT[n - 1] if live > 0.0 else 0.0
