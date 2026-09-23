"""Stand-in P0 models that give the fault framework something causal to act on.

DX CRAC units with their Controller, and control-network reachability, reduced to what a
fault needs to propagate along the Plant Design. P2 airside and P4 comm quality replace them;
the variable contract they read from faults (`constraint.*`, `quality.*`, `controller.*`)
stays.
"""

import functools
from collections.abc import Iterable

from graphene_demo_twin.plant_design import ConnectionKind, PlantDesign
from graphene_demo_twin.sim.commands import CommandSpec, command_problem
from graphene_demo_twin.sim.electrical import powered
from graphene_demo_twin.sim.engine import StepContext
from graphene_demo_twin.sim.events import Event
from graphene_demo_twin.sim.state import AssetState, WorldState
from graphene_demo_twin.sim.thermal import CRAC_TYPE, HALL_C, SUPPLY_C, served_room


class PlaceholderCracDomain:
    """DX CRAC units and their unit Controller.

    The Controller runs the unit in auto, or follows the operator's start/stop in hand, and
    loads the compressor to hold supply air at its setpoint. Equipment response follows the
    Physical Constraints faults put on the unit, and the alarm bits are the unit's own logic.
    """

    COIL_DT_K = 12.0
    """Supply air cooling below return air with the compressor fully loaded."""
    SUPPLY_TAU_S = 30.0
    NOMINAL_FAN_PCT = 80.0
    settling_s = int(6 * SUPPLY_TAU_S)
    commands = {
        CRAC_TYPE: (
            CommandSpec("mode", "Hand / auto", "mode", choices=("auto", "hand")),
            CommandSpec("run", "Start / stop (hand)", "hand_run", kind="switch"),
            CommandSpec(
                "setpoint",
                "Supply air setpoint",
                "setpoint_c",
                kind="number",
                minimum=14.0,
                maximum=28.0,
                unit="°C",
            ),
        )
    }

    def initial(self, ctx: StepContext) -> dict[str, AssetState]:
        return {
            crac: {
                "mode": "auto",
                "hand_run": True,
                "setpoint_c": SUPPLY_C,
                "run_cmd": True,
                "running": True,
                "tripped": False,
                "fan_pct": self.NOMINAL_FAN_PCT,
                "airflow": 1.0,
                "compressor_pct": 100.0 * (HALL_C - SUPPLY_C) / self.COIL_DT_K,
                "return_c": HALL_C,
                "supply_c": SUPPLY_C,
                "alarm_filter": False,
                "alarm_high_pressure": False,
                "alarm_trip": False,
                "alarm_loss_of_signal": False,
                "has_alarm": False,
            }
            for crac in assets_of(ctx.design, CRAC_TYPE)
        }

    def handles(self, event: Event, design: PlantDesign) -> bool:
        placed = design.assets.get(event.target)
        return (
            event.kind == "command"
            and placed is not None
            and placed.type_id == CRAC_TYPE
            and command_problem(self.commands[CRAC_TYPE], event.params) is None
        )

    def apply(self, event: Event, state: WorldState) -> None:
        spec = next(c for c in self.commands[CRAC_TYPE] if c.name == event.params["command"])
        value = event.params["value"]
        state.assets[event.target][spec.variable] = float(value) if spec.kind == "number" else value

    def step(self, state: WorldState, ctx: StepContext) -> None:
        for crac in assets_of(ctx.design, CRAC_TYPE):
            s = state.assets[crac]
            fan_loss = s.get("constraint.fan_loss", 0.0)
            blockage = s.get("constraint.filter_blockage", 0.0)
            trip = s.get("constraint.compressor_trip", 0.0)
            derate = s.get("constraint.condenser_derate", 0.0)
            offset = s.get("controller.setpoint_offset_c", 0.0)
            room = served_room(ctx.design, crac)
            return_c = state.assets.get(room, {}).get("temp_c", HALL_C) if room else HALL_C

            # Controller
            run_cmd = True if s["mode"] == "auto" else s["hand_run"]
            target = s["setpoint_c"] + offset
            demand = min(max((return_c - target) / self.COIL_DT_K, 0.0), 1.0)

            # Equipment: it stops without supply and restarts when supply returns.
            tripped = trip >= 0.5 or fan_loss >= 0.9
            running = run_cmd and not tripped and powered(state, ctx.design, crac)
            compressor = min(demand, 1.0 - derate) if running else 0.0
            leaving = return_c - compressor * self.COIL_DT_K
            s["run_cmd"] = run_cmd
            s["running"] = running
            s["tripped"] = tripped
            s["fan_pct"] = self.NOMINAL_FAN_PCT * (1.0 - fan_loss) if running else 0.0
            s["airflow"] = (1.0 - fan_loss) * (1.0 - blockage) if running else 0.0
            s["compressor_pct"] = 100.0 * compressor
            s["return_c"] = return_c
            s["supply_c"] += (leaving - s["supply_c"]) * ctx.dt / self.SUPPLY_TAU_S

            # Device alarm logic, reading the unit's own state
            s["alarm_filter"] = running and blockage >= 0.25
            s["alarm_high_pressure"] = trip >= 0.5 or (compressor > 0.0 and derate >= 0.4)
            s["alarm_trip"] = tripped
            s["alarm_loss_of_signal"] = s.get("comm", "good") == "bad"
            s["has_alarm"] = (
                s["alarm_filter"]
                or s["alarm_high_pressure"]
                or s["alarm_trip"]
                or s["alarm_loss_of_signal"]
            )


class PlaceholderNetworkDomain:
    """Reachability over the control network: an asset whose communication is lost, and
    every node downstream of it over `net` connections, reports `comm` uncertain or bad.
    Projection turns that into point quality."""

    settling_s = 0

    def __init__(self, fault_types: Iterable[str]) -> None:
        self._types = frozenset(fault_types)
        """Asset types that can lose communication themselves."""

    def initial(self, ctx: StepContext) -> dict[str, AssetState]:
        return {node: {"comm": "good"} for node in comm_nodes(ctx.design, self._types)}

    def handles(self, event: Event, design: PlantDesign) -> bool:
        return False

    def apply(self, event: Event, state: WorldState) -> None:
        raise AssertionError("no events")

    def step(self, state: WorldState, ctx: StepContext) -> None:
        nodes = comm_nodes(ctx.design, self._types)
        degraded: dict[str, str] = {}
        for node in nodes:
            loss = state.assets[node].get("quality.comm_loss", 0.0)
            if loss <= 0.0:
                continue
            quality = "bad" if loss >= 0.5 else "uncertain"
            reached = (node, *ctx.design.downstream(node, ConnectionKind.NET, transitive=True))
            for n in reached:
                if degraded.get(n) != "bad":
                    degraded[n] = quality
        for node in nodes:
            state.assets[node]["comm"] = degraded.get(node, "good")


# ---- Plant Design lookups (derived once per design; the design is immutable)


@functools.cache
def assets_of(design: PlantDesign, type_id: str) -> tuple[str, ...]:
    return tuple(a.path for a in design.assets.values() if a.type_id == type_id and a.room)


@functools.cache
def comm_nodes(design: PlantDesign, fault_types: frozenset[str]) -> tuple[str, ...]:
    """Every node whose communication can be lost: assets of `fault_types`, and every node
    on the control network."""
    nodes = [a.path for a in design.assets.values() if a.type_id in fault_types and a.room]
    for c in design.connections:
        if c.kind is ConnectionKind.NET:
            nodes += (c.source, c.target)
    return tuple(dict.fromkeys(nodes))
