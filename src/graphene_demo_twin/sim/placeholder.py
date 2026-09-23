"""A trivial placeholder domain that exercises the stepping loop until P1 physics replaces it."""

from graphene_demo_twin.plant_design import PlantDesign
from graphene_demo_twin.sim.engine import StepContext
from graphene_demo_twin.sim.events import Event
from graphene_demo_twin.sim.state import AssetState, WorldState


class PlaceholderHallDomain:
    """Per Data Hall: a noisy IT Load, its energy integral and a first-order air temperature
    cooled toward a fixed supply temperature, plus one placeholder fault that removes cooling.
    """

    FAULT = "placeholder.cooling_loss"
    BASE_LOAD_KW = 700.0
    LOAD_NOISE = 0.02
    """Standard deviation of IT Load, as a fraction of the base load."""
    SUPPLY_C = 18.0
    STEADY_DELTA_C = 6.0
    """Hall air temperature above supply at the base load with full cooling."""
    TAU_S = 600.0
    """Thermal time constant with full cooling."""

    UA_KW_PER_K = BASE_LOAD_KW / STEADY_DELTA_C
    CAPACITY_KJ_PER_K = UA_KW_PER_K * TAU_S

    def initial(self, ctx: StepContext) -> dict[str, AssetState]:
        return {
            hall: {
                "it_load_kw": self.BASE_LOAD_KW,
                "it_energy_kwh": 0.0,
                "temp_c": self.SUPPLY_C + self.STEADY_DELTA_C,
                "cooling_loss": 0.0,
            }
            for hall in _halls(ctx.design)
        }

    def handles(self, event: Event, design: PlantDesign) -> bool:
        if event.kind not in ("fault.inject", "fault.clear"):
            return False
        if event.params.get("fault") != self.FAULT or event.target not in _halls(design):
            return False
        severity = event.params.get("severity", 1.0)
        return isinstance(severity, int | float) and 0.0 <= severity <= 1.0

    def apply(self, event: Event, state: WorldState) -> None:
        hall = state.assets[event.target]
        if event.kind == "fault.inject":
            hall["cooling_loss"] = float(event.params.get("severity", 1.0))
        else:
            hall["cooling_loss"] = 0.0

    def step(self, state: WorldState, ctx: StepContext) -> None:
        for hall in _halls(ctx.design):
            s = state.assets[hall]
            load = self.BASE_LOAD_KW * (1.0 + self.LOAD_NOISE * ctx.gauss(f"{hall}/it_load"))
            cooling = self.UA_KW_PER_K * (1.0 - s["cooling_loss"]) * (s["temp_c"] - self.SUPPLY_C)
            s["it_load_kw"] = load
            s["it_energy_kwh"] += load * ctx.dt / 3600.0
            s["temp_c"] += (load - cooling) * ctx.dt / self.CAPACITY_KJ_PER_K


def _halls(design: PlantDesign) -> tuple[str, ...]:
    return tuple(r.id for r in design.rooms.values() if r.kind == "hall")
