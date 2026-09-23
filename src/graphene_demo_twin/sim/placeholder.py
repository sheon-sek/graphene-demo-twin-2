"""Stand-in P0 models that give the fault framework something causal to act on.

Data Hall air temperature, DX CRAC units with their Controller, hall temperature sensors and
control-network reachability, all reduced to what a fault needs to propagate along the Plant
Design. P1 (thermal, IT Load) and P2 (airside) replace them; the variable contract they read
from faults (`constraint.*`, `observation.*`, `quality.*`, `controller.*`) stays.
"""

import functools
from collections.abc import Iterable

from graphene_demo_twin.plant_design import ConnectionKind, PlantDesign
from graphene_demo_twin.sim.commands import CommandSpec, command_problem
from graphene_demo_twin.sim.engine import StepContext
from graphene_demo_twin.sim.events import Event
from graphene_demo_twin.sim.state import AssetState, WorldState

SUPPLY_C = 18.0
"""Supply air temperature every air supplier delivers at steady state."""
HALL_C = 24.0
"""Steady-state Data Hall air temperature (supply plus the design air temperature rise)."""

CRAC_TYPE = "CRAC"
SENSOR_TYPE = "Temperature and Humidity"
AIR_WEIGHT = {CRAC_TYPE: 3.0}
"""Share of a hall's cooling per air supplier type, relative to 1 for any other supplier."""


class PlaceholderHallDomain:
    """Per Data Hall: a noisy IT Load, its energy integral and a first-order air temperature.
    All IT Load becomes heat in the hall; the air suppliers connected to it in the Plant Design
    remove it in proportion to their airflow and supply temperature.
    """

    BASE_LOAD_KW = 700.0
    LOAD_NOISE = 0.02
    """Standard deviation of IT Load, as a fraction of the base load."""
    TAU_S = 600.0
    """Thermal time constant with full cooling."""
    settling_s = int(6 * TAU_S)
    """Time for a disturbance to die out to within 0.25 %."""

    UA_KW_PER_K = BASE_LOAD_KW / (HALL_C - SUPPLY_C)
    CAPACITY_KJ_PER_K = UA_KW_PER_K * TAU_S

    def initial(self, ctx: StepContext) -> dict[str, AssetState]:
        return {
            hall: {
                "it_load_kw": self.BASE_LOAD_KW,
                "it_energy_kwh": 0.0,
                "temp_c": HALL_C,
                "cooling_kw": self.BASE_LOAD_KW,
            }
            for hall in halls(ctx.design)
        }

    def handles(self, event: Event, design: PlantDesign) -> bool:
        return False

    def apply(self, event: Event, state: WorldState) -> None:
        raise AssertionError("no events")

    def step(self, state: WorldState, ctx: StepContext) -> None:
        for hall in halls(ctx.design):
            s = state.assets[hall]
            temp = s["temp_c"]
            load = self.BASE_LOAD_KW * (1.0 + self.LOAD_NOISE * ctx.gauss(f"{hall}/it_load"))
            cooling = 0.0
            for supplier, share in air_suppliers(ctx.design, hall):
                unit = state.assets.get(supplier)
                if unit is not None and "airflow" in unit:
                    cooling += share * unit["airflow"] * (temp - unit["supply_c"])
                else:  # not modelled yet: a steady supplier
                    cooling += share * (temp - SUPPLY_C)
            cooling *= self.UA_KW_PER_K
            s["it_load_kw"] = load
            s["it_energy_kwh"] += load * ctx.dt / 3600.0
            s["cooling_kw"] = cooling
            s["temp_c"] = temp + (load - cooling) * ctx.dt / self.CAPACITY_KJ_PER_K


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

            # Equipment
            tripped = trip >= 0.5 or fan_loss >= 0.9
            running = run_cmd and not tripped
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


class PlaceholderSensorDomain:
    """Temperature sensors in the Data Halls, reading their hall's air temperature through
    whatever observation corruption a sensor fault applies."""

    settling_s = 0

    def initial(self, ctx: StepContext) -> dict[str, AssetState]:
        return {sensor: {"temp_c": HALL_C} for sensor in hall_sensors(ctx.design)}

    def handles(self, event: Event, design: PlantDesign) -> bool:
        return False

    def apply(self, event: Event, state: WorldState) -> None:
        raise AssertionError("no events")

    def step(self, state: WorldState, ctx: StepContext) -> None:
        for sensor, hall in hall_sensors(ctx.design).items():
            s = state.assets[sensor]
            if s.get("observation.stuck", 0.0) >= 0.5:
                continue  # frozen on its last reading
            s["temp_c"] = state.assets[hall]["temp_c"] + s.get("observation.bias_c", 0.0)


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
def halls(design: PlantDesign) -> tuple[str, ...]:
    return tuple(r.id for r in design.rooms.values() if r.kind == "hall")


@functools.cache
def assets_of(design: PlantDesign, type_id: str) -> tuple[str, ...]:
    return tuple(a.path for a in design.assets.values() if a.type_id == type_id and a.room)


@functools.cache
def air_suppliers(design: PlantDesign, room: str) -> tuple[tuple[str, float], ...]:
    """The units supplying air to `room` and each one's share of its cooling."""
    suppliers = design.upstream(room, ConnectionKind.AIR)
    weights = [AIR_WEIGHT.get(_type_of(design, n), 1.0) for n in suppliers]
    total = sum(weights)
    return tuple((n, w / total) for n, w in zip(suppliers, weights, strict=True))


@functools.cache
def served_room(design: PlantDesign, unit: str) -> str | None:
    rooms = [n for n in design.downstream(unit, ConnectionKind.AIR) if design.is_room(n)]
    return rooms[0] if rooms else None


@functools.cache
def hall_sensors(design: PlantDesign) -> dict[str, str]:
    """Temperature sensor → the Data Hall it sits in."""
    in_halls = set(halls(design))
    return {
        a.path: a.room
        for a in design.assets.values()
        if a.type_id == SENSOR_TYPE and a.room in in_halls
    }


@functools.cache
def comm_nodes(design: PlantDesign, fault_types: frozenset[str]) -> tuple[str, ...]:
    """Every node whose communication can be lost: assets of `fault_types`, and every node
    on the control network."""
    nodes = [a.path for a in design.assets.values() if a.type_id in fault_types and a.room]
    for c in design.connections:
        if c.kind is ConnectionKind.NET:
            nodes += (c.source, c.target)
    return tuple(dict.fromkeys(nodes))


def _type_of(design: PlantDesign, node: str) -> str:
    placed = design.assets.get(node)
    return "" if placed is None else placed.type_id
