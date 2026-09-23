"""Stand-in P0 models that give the fault framework something causal to act on.

Data Hall air temperature, DX CRAC units with their Controller, hall temperature sensors and
control-network reachability, all reduced to what a fault needs to propagate along the Plant
Design. P1 thermal zones (#20) and P2 airside replace them; the variable contract they read
from faults (`constraint.*`, `observation.*`, `quality.*`, `controller.*`) stays.
"""

import functools
import hashlib
from collections.abc import Iterable

from graphene_demo_twin.plant_design import ConnectionKind, PlantDesign
from graphene_demo_twin.sim.commands import CommandSpec, command_problem
from graphene_demo_twin.sim.engine import StepContext
from graphene_demo_twin.sim.events import Event
from graphene_demo_twin.sim.it_load import it_equipment, it_heat_kw, it_utilisation
from graphene_demo_twin.sim.state import AssetState, WorldState
from graphene_demo_twin.sim.weather import outdoor_air, relative_humidity, site_air

SUPPLY_C = 18.0
"""Supply air temperature every air supplier delivers at steady state."""
HALL_C = 24.0
"""Steady-state Data Hall air temperature (supply plus the design air temperature rise)."""

CRAC_TYPE = "CRAC"
SENSOR_TYPE = "Temperature and Humidity"
COLD_AISLE_SENSOR_TYPE = "Environment Monitoring"
COLD_AISLE_SPREAD_C = 0.6
"""Largest difference between one cold-aisle spot and the aisle's mean."""
AIR_WEIGHT = {CRAC_TYPE: 3.0}
"""Share of a hall's cooling per air supplier type, relative to 1 for any other supplier."""


class PlaceholderHallDomain:
    """Per Data Hall: a first-order air temperature and the moisture in it. All of the IT
    Load becomes heat in the hall; the air suppliers connected to it in the Plant Design
    remove it in proportion to their airflow and supply temperature. The fresh-air handlers
    hold its dew point low, a little above it on humid days.
    """

    NOMINAL_UTILISATION = 0.675
    """Share of design IT Load at which the hall sits at HALL_C with full cooling."""
    TAU_S = 600.0
    """Thermal time constant with full cooling."""
    DEW_TAU_S = TAU_S
    DEW_POINT_C = 11.0
    """Hall dew point the fresh-air handlers hold on a day with a 24.35 °C outdoor dew point."""
    DEW_LEAK = 0.2
    """How much of an outdoor dew point change leaks into the halls."""
    RECIRCULATION = 0.15
    """Share of hot-aisle air that reaches the cold aisle."""
    settling_s = int(6 * TAU_S)
    """Time for a disturbance to die out to within 0.25 %."""

    def initial(self, ctx: StepContext) -> dict[str, AssetState]:
        air = outdoor_air(ctx.noise, ctx.time)
        states = {}
        for hall in halls(ctx.design):
            heat = sum(
                b.design_kw * it_utilisation(ctx.noise, b, ctx.time)
                for n, b in it_equipment(ctx.design).items()
                if ctx.design.asset(n).room == hall
            )
            temp = SUPPLY_C + heat / ua_kw_per_k(ctx.design, hall)
            states[hall] = {
                "it_heat_kw": heat,
                "temp_c": temp,
                "cooling_kw": heat,
                "cold_aisle_c": SUPPLY_C + self.RECIRCULATION * (temp - SUPPLY_C),
                "dew_point_c": self._dew_target(air.dew_point_c),
            }
        return states

    def handles(self, event: Event, design: PlantDesign) -> bool:
        return False

    def apply(self, event: Event, state: WorldState) -> None:
        raise AssertionError("no events")

    def step(self, state: WorldState, ctx: StepContext) -> None:
        outdoor = site_air(state, ctx.design)["dew_point_c"]
        for hall in halls(ctx.design):
            s = state.assets[hall]
            temp = s["temp_c"]
            heat = it_heat_kw(state, ctx.design, hall)
            ua = ua_kw_per_k(ctx.design, hall)
            cooling = flow = supply = 0.0
            for supplier, share in air_suppliers(ctx.design, hall):
                airflow, supply_c = supplier_air(state, supplier)
                cooling += share * airflow * (temp - supply_c)
                flow += share * airflow
                supply += share * airflow * supply_c
            mixed = supply / flow if flow > 0.0 else temp
            s["it_heat_kw"] = heat
            s["cooling_kw"] = cooling * ua
            s["temp_c"] = temp + (heat - cooling * ua) * ctx.dt / (ua * self.TAU_S)
            s["cold_aisle_c"] = mixed + self.RECIRCULATION * (s["temp_c"] - mixed)
            dew = s["dew_point_c"]
            s["dew_point_c"] = dew + (self._dew_target(outdoor) - dew) * ctx.dt / self.DEW_TAU_S

    def _dew_target(self, outdoor_dew_c: float) -> float:
        return self.DEW_POINT_C + self.DEW_LEAK * (outdoor_dew_c - 24.35)


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
    """Sensors in the Data Halls. Temperature and Humidity sensors read their hall's air
    temperature through whatever observation corruption a sensor fault applies; Environment
    Monitoring sensors read the cold aisle where they hang (a fixed offset per spot) and its
    relative humidity."""

    settling_s = 0

    def initial(self, ctx: StepContext) -> dict[str, AssetState]:
        states: dict[str, AssetState] = {s: {"temp_c": HALL_C} for s in hall_sensors(ctx.design)}
        for sensor in cold_aisle_sensors(ctx.design):
            states[sensor] = {"temp_c": HALL_C, "rh_pct": 50.0}
        return states

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
        for sensor, (hall, offset) in cold_aisle_sensors(ctx.design).items():
            h = state.assets[hall]
            s = state.assets[sensor]
            s["temp_c"] = h["cold_aisle_c"] + offset
            s["rh_pct"] = relative_humidity(s["temp_c"], h["dew_point_c"])


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
def cold_aisle_sensors(design: PlantDesign) -> dict[str, tuple[str, float]]:
    """Environment Monitoring sensor → the Data Hall it sits in and how much warmer its spot
    in the cold aisle runs than the aisle's mean, fixed by where it hangs."""
    in_halls = set(halls(design))
    return {
        a.path: (a.room, COLD_AISLE_SPREAD_C * (2.0 * _spot(a.path) - 1.0))
        for a in design.assets.values()
        if a.type_id == COLD_AISLE_SENSOR_TYPE and a.room in in_halls
    }


def _spot(path: str) -> float:
    return int.from_bytes(hashlib.blake2b(path.encode(), digest_size=4).digest()) / 2**32


@functools.cache
def ua_kw_per_k(design: PlantDesign, hall: str) -> float:
    """Heat a hall's air suppliers remove per kelvin of hall air above supply, at full flow."""
    nominal = design.it_basis[hall].design_kw * PlaceholderHallDomain.NOMINAL_UTILISATION
    return nominal / (HALL_C - SUPPLY_C)


def supplier_air(state: WorldState, supplier: str) -> tuple[float, float]:
    """(airflow as a fraction of nominal, supply temperature) of an air supplier; a unit
    not modelled yet is steady at full flow and SUPPLY_C."""
    unit = state.assets.get(supplier)
    if unit is not None and "airflow" in unit:
        return unit["airflow"], unit["supply_c"]
    return 1.0, SUPPLY_C


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
