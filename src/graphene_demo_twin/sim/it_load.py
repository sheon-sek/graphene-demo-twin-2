"""IT Load: the electrical power the IT equipment in each Data Hall draws, all of it heat.

Each hall's IT equipment is an Unexported Asset (`~IT-DHnn`) at the end of its three branch
circuits. It follows a profile from the Plant Design basis: a share of the hall's design load
between its operating bounds, with a diurnal and a weekly shape and a slow per-hall wander.
An external fault can surge it towards the hall's design load. With every branch dead the
equipment goes down and draws nothing.
"""

import functools
import math

from graphene_demo_twin.plant_design import ITBasis, PlantDesign
from graphene_demo_twin.sim.electrical import powered
from graphene_demo_twin.sim.engine import StepContext
from graphene_demo_twin.sim.events import Event
from graphene_demo_twin.sim.noise import Noise
from graphene_demo_twin.sim.state import AssetState, WorldState
from graphene_demo_twin.sim.weather import DAY_S, LOCAL_OFFSET_S

IT_TYPE = "IT Load"

_BIAS = 0.3
"""How far each hall's own level sits from the middle of its operating band."""
_DIURNAL = 0.3
"""Daily swing, peaking mid-afternoon (local time)."""
_WEEKDAY, _WEEKEND = 0.05, -0.25
_WANDER = 0.15
_JITTER = 0.03
"""Shape terms, as fractions of half the operating band."""


def it_utilisation(noise: Noise, basis: ITBasis, time: int) -> float:
    """The Base World's IT Load in `basis.hall` at `time`, as a fraction of its design load."""
    local = time + LOCAL_OFFSET_S
    hour = (local % DAY_S) / 3600.0
    shape = (
        _BIAS * (2.0 * noise.held(f"it/{basis.hall}/level", 0) - 1.0)
        + _DIURNAL * math.cos(2.0 * math.pi * (hour - 15.0) / 24.0)
        + _WEEKDAY
        + (_WEEKEND - _WEEKDAY) * _weekend(local)
        + _WANDER * noise.smooth(f"it/{basis.hall}", time, 3600)
        + _JITTER * noise.smooth(f"it/{basis.hall}/jitter", time, 120)
    )
    mid = 0.5 * (basis.operating_min + basis.operating_max)
    half = 0.5 * (basis.operating_max - basis.operating_min)
    return min(max(mid + half * shape, basis.operating_min), basis.operating_max)


def _weekend(local: int) -> float:
    """1 over the weekend and 0 on weekdays, easing over the first four hours of Saturday
    and of Monday."""
    weekday = (local // DAY_S + 3) % 7  # 1970-01-01 was a Thursday; 0 is Monday
    ease = min((local % DAY_S) / (4 * 3600.0), 1.0)
    ease = ease * ease * (3.0 - 2.0 * ease)
    return {5: ease, 6: 1.0, 0: 1.0 - ease}.get(weekday, 0.0)


class ITLoadDomain:
    """The IT equipment in every Data Hall: its power draw and the energy it has used."""

    settling_s = 0

    def initial(self, ctx: StepContext) -> dict[str, AssetState]:
        return {
            node: {
                "design_kw": basis.design_kw,
                "utilisation": (u := it_utilisation(ctx.noise, basis, ctx.time)),
                "power_kw": basis.design_kw * u,
                "energy_kwh": 0.0,
            }
            for node, basis in it_equipment(ctx.design).items()
        }

    def handles(self, event: Event, design: PlantDesign) -> bool:
        return False

    def apply(self, event: Event, state: WorldState) -> None:
        raise AssertionError("no events")

    def step(self, state: WorldState, ctx: StepContext) -> None:
        for node, basis in it_equipment(ctx.design).items():
            s = state.assets[node]
            u = it_utilisation(ctx.noise, basis, ctx.time) + s.get("constraint.load_surge", 0.0)
            s["utilisation"] = min(u, 1.0)
            up = powered(state, ctx.design, node)
            s["power_kw"] = basis.design_kw * s["utilisation"] if up else 0.0
            s["energy_kwh"] += s["power_kw"] * ctx.dt / 3600.0


@functools.cache
def it_equipment(design: PlantDesign) -> dict[str, ITBasis]:
    """IT equipment node → the design basis of the hall it sits in."""
    return {
        a.path: design.it_basis[a.room]
        for a in design.assets.values()
        if a.type_id == IT_TYPE and a.room in design.it_basis
    }


@functools.cache
def it_in(design: PlantDesign, hall: str) -> tuple[str, ...]:
    """The IT equipment nodes in `hall`."""
    return tuple(n for n in it_equipment(design) if design.asset(n).room == hall)


def it_heat_kw(state: WorldState, design: PlantDesign, hall: str) -> float:
    """Heat the IT equipment puts into `hall` this step: all of its power."""
    return sum(state.assets[n]["power_kw"] for n in it_in(design, hall))
