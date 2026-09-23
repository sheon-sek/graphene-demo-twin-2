"""The fault domain: active faults as world state, and the variables they drive."""

from graphene_demo_twin.faults.catalog import INJECT, FaultCatalog, FaultParams, fault_key
from graphene_demo_twin.plant_design import PlantDesign
from graphene_demo_twin.sim import AssetState, Event, StepContext, WorldState


class FaultDomain:
    """Takes fault inject and clear events into `WorldState.faults` and, every step, drives
    each active fault's variable on its asset (a Physical Constraint, an observation
    corruption, a quality loss or a Controller error) at the fault's current level.

    It writes nothing else: device and Controller models read these variables, and alarm
    bits come from their logic. Register it first, so every model sees this step's levels.
    """

    settling_s = 0

    def __init__(self, catalog: FaultCatalog) -> None:
        self.catalog = catalog

    def initial(self, ctx: StepContext) -> dict[str, AssetState]:
        return {}

    def handles(self, event: Event, design: PlantDesign) -> bool:
        return self.catalog.check(event, design) is None

    def apply(self, event: Event, state: WorldState) -> None:
        fault = event.params["fault"]
        key = fault_key(event.target, fault)
        self._drop(state, key)
        if event.kind == INJECT:
            params = FaultParams.parse(event.params)
            state.faults[key] = {
                "fault": fault,
                "target": event.target,
                "severity": params.severity,
                "since": event.at,
                "ramp_s": params.ramp_s,
                "until": None if params.auto_clear_s is None else event.at + params.auto_clear_s,
                "level": 0.0,
            }

    def step(self, state: WorldState, ctx: StepContext) -> None:
        driven: dict[tuple[str, str], float] = {}
        for key, fault in list(state.faults.items()):
            if fault["until"] is not None and ctx.time >= fault["until"]:
                self._drop(state, key)  # auto-clear
                continue
            ramp_s, severity = fault["ramp_s"], fault["severity"]
            elapsed = ctx.time - fault["since"]
            level = severity if elapsed >= ramp_s else severity * elapsed / ramp_s
            fault["level"] = level
            spec = self.catalog.get(fault["fault"])
            slot = (fault["target"], spec.variable)
            driven[slot] = driven.get(slot, 0.0) + level * spec.span
        for (target, variable), value in driven.items():
            state.assets.setdefault(target, {})[variable] = value

    def _drop(self, state: WorldState, key: str) -> None:
        """Remove a fault and the variable it drove; another fault on the same variable
        drives it again in the next step."""
        fault = state.faults.pop(key, None)
        if fault is None:
            return
        target = fault["target"]
        variables = state.assets.get(target)
        if variables is None:
            return
        variables.pop(self.catalog.get(fault["fault"]).variable, None)
        if not variables:
            del state.assets[target]
