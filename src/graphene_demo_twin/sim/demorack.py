"""The Demo Rack (P4): a standalone cabinet with its own incomer, deliberately not connected to
the site's electrical network.

The incomer's E820 multi-circuit meter watches 126 branch circuits (six banks of 21: Ba1–Ba3
and Bb1–Bb3, one bank per phase) grouped nine to a breaker, 14 breakers in all. Seven
demo meters watch groups of those breakers. Everything is derived in one pass from the circuit
loads and the breakers' positions, so every reading, per circuit, per breaker, per meter and at
the incomer, always adds up.

Faults act through variables on the rack's own assets: `constraint.breaker_trip` opens a
breaker, `constraint.earth_fault` makes it sense leakage and open, and
`constraint.incomer_trip` drops the whole rack. A tripped breaker recloses by itself
`RECLOSE_S` after its fault clears. Nothing here reads or writes any other asset.
"""

import math
from dataclasses import dataclass

from graphene_demo_twin.sim.engine import StepContext
from graphene_demo_twin.sim.events import Event
from graphene_demo_twin.sim.state import AssetState, WorldState

DEMO = "DemoRack"
INCOMER = f"{DEMO}/E820"
BREAKERS = tuple(f"{DEMO}/Breaker{n}" for n in range(1, 15))
BANKS = ("Ba1", "Ba2", "Ba3", "Bb1", "Bb2", "Bb3")
SLOTS = 21
CIRCUITS = len(BANKS) * SLOTS
PER_BREAKER = CIRCUITS // len(BREAKERS)
V_LN = 230.0
HZ = 50.0
DC_V = 48.0
DC_SHARE = 0.15
"""Share of the last breaker's power that a rectifier hands to the DC bus GDC230 watches."""
RECTIFIER_EFF = 0.95
FEEDER_DROP_PER_KVA = 0.0004
"""Fraction of voltage lost per kVA on a phase."""
RECLOSE_S = 30
PHASE_V = (0.002, -0.003, 0.001)


@dataclass(frozen=True, slots=True)
class Meter:
    breakers: tuple[int, ...]
    """Indices into BREAKERS of the circuits it measures."""
    phases: int
    dc: bool = False


METERS: dict[str, Meter] = {
    f"{DEMO}/GEM630": Meter((0, 1, 2, 3), 3),
    f"{DEMO}/GPQM144 Pro": Meter((4, 5, 6), 3),
    f"{DEMO}/GPQM96": Meter((7, 8, 9), 3),
    f"{DEMO}/GPM96": Meter((10, 11), 3),
    f"{DEMO}/GEM130": Meter((12,), 3),
    f"{DEMO}/GEM230": Meter((13,), 1),
    f"{DEMO}/GDC230": Meter((13,), 1, dc=True),
}


def circuit_name(k: int) -> str:
    return f"{BANKS[k // SLOTS]}_I{k % SLOTS + 1:02d}"


def circuit_breaker(k: int) -> int:
    return k // PER_BREAKER


def circuit_phase(k: int) -> int:
    return (k // SLOTS) % 3


def circuit_base_kw(k: int) -> float:
    """Rated draw of one circuit; every seventh slot is empty."""
    return 0.0 if k % 7 == 6 else 0.4 + 1.6 * ((k * 37) % 17) / 16.0


def circuit_pf(k: int) -> float:
    return 0.92 + 0.06 * ((k * 13) % 7) / 6.0


def circuit_tan(k: int) -> float:
    return math.tan(math.acos(circuit_pf(k)))


_KW_KEYS = tuple(f"c{k}_kw" for k in range(CIRCUITS))
_KWH_KEYS = tuple(f"c{k}_kwh" for k in range(CIRCUITS))
_BASE = tuple(circuit_base_kw(k) for k in range(CIRCUITS))
_TAN = tuple(circuit_tan(k) for k in range(CIRCUITS))
_BREAKER_OF = tuple(circuit_breaker(k) for k in range(CIRCUITS))
_PHASE_OF = tuple(circuit_phase(k) for k in range(CIRCUITS))
_CIRCUITS_OF = tuple(
    tuple(k for k in range(CIRCUITS) if _BREAKER_OF[k] == b) for b in range(len(BREAKERS))
)
_WOBBLE_KEYS = tuple(f"demorack/b{b}" for b in range(len(BREAKERS)))


class DemoRackDomain:
    """The Demo Rack's electrical system. It touches only `DemoRack/` assets."""

    settling_s = 0

    def initial(self, ctx: StepContext) -> dict[str, AssetState]:
        states: dict[str, AssetState] = {
            INCOMER: {"live": True, "energy_kwh": 0.0, "hz": HZ, "has_alarm": False},
        }
        for k in range(CIRCUITS):
            states[INCOMER][_KW_KEYS[k]] = 0.0
            states[INCOMER][_KWH_KEYS[k]] = 100.0 + k
        for b in BREAKERS:
            states[b] = {
                "trip": False,
                "ef": False,
                "closed": True,
                "reclose_s": 0,
                "p_kw": 0.0,
                "q_kvar": 0.0,
            }
        for m in METERS:
            states[m] = {"energy_kwh": 50.0, "has_alarm": False, "hz": HZ}
        _flow(states, ctx, integrate=False)
        return states

    def handles(self, event: Event, design: object) -> bool:
        return False

    def apply(self, event: Event, state: WorldState) -> None:
        raise NotImplementedError

    def step(self, state: WorldState, ctx: StepContext) -> None:
        _flow(state.assets, ctx, integrate=True)


def _flow(a: dict[str, AssetState], ctx: StepContext, *, integrate: bool) -> None:
    hours = ctx.dt / 3600.0 if integrate else 0.0
    inc = a[INCOMER]
    live = inc.get("constraint.incomer_trip", 0.0) < 0.5
    inc["live"] = live
    v_pu = 1.0 + 0.004 * ctx.noise.smooth("demorack/v", ctx.time, 600)
    hz = HZ + 0.02 * ctx.noise.smooth("demorack/hz", ctx.time, 300) if live else 0.0
    inc["hz"] = hz

    any_tripped = False
    breakers = [a[b] for b in BREAKERS]
    for s in breakers:
        fault = s.get("constraint.breaker_trip", 0.0) >= 0.5
        ef = s.get("constraint.earth_fault", 0.0) >= 0.5
        if fault or ef:
            s["trip"], s["reclose_s"] = True, 0
        elif s["trip"]:
            s["reclose_s"] += int(ctx.dt)
            if s["reclose_s"] >= RECLOSE_S:
                s["trip"] = False
        s["ef"] = ef
        s["closed"] = not s["trip"]
        any_tripped = any_tripped or bool(s["trip"])

    phase_p, phase_q = [0.0] * 3, [0.0] * 3
    for n, b in enumerate(breakers):
        powered = live and b["closed"]
        wobble = 1.0 + 0.08 * ctx.noise.smooth(_WOBBLE_KEYS[n], ctx.time, 900) if powered else 0.0
        bp = bq = 0.0
        for k in _CIRCUITS_OF[n]:
            kw = _BASE[k] * wobble
            inc[_KW_KEYS[k]] = kw
            inc[_KWH_KEYS[k]] += kw * hours
            q = kw * _TAN[k]
            bp += kw
            bq += q
            phase_p[_PHASE_OF[k]] += kw
            phase_q[_PHASE_OF[k]] += q
        b["p_kw"], b["q_kvar"] = bp, bq

    volts = [
        V_LN * v_pu * (1.0 + PHASE_V[k]) * (1.0 - FEEDER_DROP_PER_KVA * math.hypot(p, q))
        if live
        else 0.0
        for k, (p, q) in enumerate(zip(phase_p, phase_q, strict=True))
    ]
    for k in range(3):
        inc[f"p{k + 1}"], inc[f"q{k + 1}"], inc[f"v{k + 1}"] = phase_p[k], phase_q[k], volts[k]
    inc["energy_kwh"] = inc["energy_kwh"] + sum(phase_p) * hours
    inc["has_alarm"] = (not live) or any_tripped

    for m, meter in METERS.items():
        s = a[m]
        p, q = [0.0] * 3, [0.0] * 3
        tripped = False
        for i in meter.breakers:
            tripped = tripped or bool(breakers[i]["trip"])
            for k in _CIRCUITS_OF[i]:
                kw = inc[_KW_KEYS[k]]
                slot = 0 if meter.phases == 1 else _PHASE_OF[k]
                p[slot] += kw
                q[slot] += kw * _TAN[k]
        v = list(volts) if meter.phases == 3 else [volts[0], 0.0, 0.0]
        if meter.dc:
            p = [DC_SHARE * RECTIFIER_EFF * p[0], 0.0, 0.0]
            q = [0.0, 0.0, 0.0]
            v = [DC_V if live else 0.0, 0.0, 0.0]
        for k in range(3):
            s[f"p{k + 1}"], s[f"q{k + 1}"], s[f"v{k + 1}"] = p[k], q[k], v[k]
        s["hz"] = 0.0 if meter.dc else hz
        s["energy_kwh"] = s["energy_kwh"] + sum(p) * hours
        s["has_alarm"] = (not live) or tripped
        s["live"] = live
