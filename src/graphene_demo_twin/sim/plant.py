"""The chiller plant (ADR-0004) and its Controllers.

One plant with four water-cooled centrifugal chillers, CH-001–003 (`Chiller/R_C1`–`3`) and
the Unexported Asset CH-004. Each chiller is one leg: its primary chilled-water pump, its
condenser-water pump, evaporator and condenser isolation valves, a header valve, two
stratified buffer tanks on its supply, and a Tower Group of five cells rejecting its heat.
The legs feed a common supply header. A variable-speed secondary pump under a DP PID
distributes chilled water to the air units, and a decoupler with four bypass valves under a
bypass PID joins the supply and return headers: surplus primary flow passes it back to the
return, and a deficit draws return water forward into the supply, warming it.

Hydraulics are algebraic each step (pump speeds and valve positions set the flows), and
temperatures are integrated: the supply and return headers, every buffer tank layer and
every tower basin hold water. A chiller removes what it takes to bring its entering water
to setpoint, up to its capacity, the load limit and its soft-load ramp; its lift, COP,
refrigerant pressures and discharge temperatures follow from its loading and the condenser
water its towers return. Tower fans modulate to hold condenser water at setpoint, bounded
below by the wet bulb.

Controllers: each chiller runs a start/stop sequence (open valves, start pumps, prove
flow, soft load; unload, pump run-on, close valves) in auto from the sequencer or in hand
from the operator, with its own safety trips. The sequencer stages chillers from demand,
bounded by the minimum and maximum chillers, after a stage-up or stage-down wait; it
replaces a chiller that becomes unavailable at once, and the rotation schedule hands the
lead on, starting the new set before stopping the old. Command variables (`run_cmd`,
`cmd_pct`, `cv_pct`) are Controller outputs; `running`, `pos_pct`, `hz` and `speed_pct`
are how the equipment responded.

Physical Constraints the plant reads (the `faults` catalog): on a chiller
`constraint.trip`, `constraint.compressor_degradation` and `constraint.condenser_fouling`;
on a pump `constraint.trip` and `constraint.bearing_wear`; on a tower cell
`constraint.fan_loss`, `constraint.group_fan_loss` (every cell of its Tower Group) and
`constraint.fill_fouling`; on a valve `constraint.stuck`. Controller
faults: `controller.hand_mode` on a chiller (its selector left in hand and on) and
`controller.gain_factor` on the secondary pump (a mistuned DP PID). A chiller's
`observation.drift_c_per_h` corrupts only its leaving-water sensor (`chws_read_c`).
"""

import functools
import math
import re
import time
from dataclasses import dataclass

from graphene_demo_twin.plant_design import ConnectionKind, PlantDesign
from graphene_demo_twin.sim.commands import (
    HAND_AUTO,
    HAND_AUTO_STATE,
    CommandSpec,
    command_problem,
)
from graphene_demo_twin.sim.electrical import network
from graphene_demo_twin.sim.engine import StepContext
from graphene_demo_twin.sim.events import Event
from graphene_demo_twin.sim.state import AssetState, WorldState
from graphene_demo_twin.sim.thermal import (
    COIL_APPROACH_K,
    CRAC_TYPE,
    FRESH_AIR_TYPE,
    PLANT,
    SUPPLY_C,
    air_suppliers,
    ua_kw_per_k,
    zones,
)
from graphene_demo_twin.sim.weather import (
    DAY_S,
    LOCAL_OFFSET_S,
    enthalpy_kj_per_kg,
    site_air,
)

__all__ = ["PLANT", "ChillerPlantDomain", "plant_layout"]

CHILLER_TYPE = "Chiller"
PUMP_TYPE = "Chiller Pump"
VALVE_TYPE = "Chiller Valve"
TANK_TYPE = "Buffer Tank"
TOWER_TYPE = "Cooling Tower"
TANK_COMMANDS = (
    CommandSpec("valves", "Valves hand / auto", "valve_mode", choices=("auto", "hand")),
    CommandSpec("bypass", "Bypass the tank (hand)", "hand_bypass", kind="switch"),
)
TANK_VALVES_INITIAL: dict[str, str | bool] = {
    "valve_mode": "auto",
    "hand_bypass": False,
    "no_cmd": True,
    "nc_cmd": False,
    "no_open": True,
    "nc_open": False,
    "no_fail": False,
    "nc_fail": False,
}
"""A buffer tank's valves as it starts: in line, the bypass shut, under its Controller."""
CONTROLLER_TYPE = "Chiller Plant Controller"
"""The plant Controller (`PLANT`): the sequencer and the PIDs, and their setpoints."""

CP = 4.186
"""Specific heat of water, kJ/(kg·K); a litre is a kilogram."""
CHILLER_KWR = 3500.0
"""Rated capacity of each chiller."""
CHWS_SP_C, CHW_DT_K = 14.0, 6.0
"""Chilled water supply setpoint and design temperature difference (14 / 20 °C)."""
CWS_SP_C, APPROACH_SP_K = 29.0, 3.0
"""Condenser water supply setpoint, and the tower approach it may not undercut."""
LOAD_LIMIT_PCT = 85.0
LEG_CHW_LPS = CHILLER_KWR / (CP * CHW_DT_K)
"""Primary chilled-water flow of one leg, fixed by its pump speed."""
LEG_CW_LPS = 1.18 * CHILLER_KWR / (CP * 5.0)
"""Condenser-water flow of one leg: the heat of a fully loaded chiller over 5 K."""
CHW_HZ, CW_HZ, PUMP_HZ_PER_S = 48.0, 50.0, 5.0
"""Constant-speed primary and condenser pumps, and how fast their drives ramp."""
CHW_PUMP_KW, CW_PUMP_KW, SECONDARY_PUMP_KW = 4.0, 6.0, 45.0

# Secondary loop: pump head H0·s², pipework R·Q², and valves at the units that take the
# flow their demand needs at the DP setpoint.
DP_SP_KPA, MIN_DP_KPA, PUMP_MIN_SPEED_PCT = 85.0, 40.0, 30.0
SECONDARY_HEAD_KPA, SECONDARY_DESIGN_LPS = 250.0, 420.0
PIPE_R = 100.0 / SECONDARY_DESIGN_LPS**2
MIN_FLOW_LPS = 0.1 * SECONDARY_DESIGN_LPS
"""Least flow the units' valves pass; the plant's minimum flow."""
DP_KP, DP_KI = 20.0, 2.0
"""DP PID gains: % per unit of relative error, and % per second per unit."""
# Decoupler: surplus primary flow through the bypass valves raises the header DP.
BYPASS_SP_KPA, BYPASS_SHUTOFF_KPA, BYPASS_CV_LPS = 65.0, 200.0, 700.0
BYPASS_KP, BYPASS_KI = 20.0, 1.0
VALVE_STROKE_S, BYPASS_STROKE_S = 60.0, 30.0
HEADER_KG = 60_000.0
"""Water in the supply header and in the return header, each."""

TANK_KG, LAYERS = 30_000.0, 6
LAYER_KEYS = tuple(f"t{k}" for k in range(1, LAYERS + 1))
"""A buffer tank's layer temperatures, top (1) to bottom: the flow enters at the bottom."""
RECHARGE_LPS = 30.0
"""Flow that re-cools a standby leg's tank from the supply header."""
RECHARGE_START_K, RECHARGE_STOP_K = 0.3, 0.02
"""How far above the supply a standby tank may warm on average before it is recharged, and
how close to it recharging brings its warmest layer."""
TANK_ALARM_K = 3.0
FAN_TRIP_LOSS = 0.9
"""Airflow a tower cell's fan may lose before it trips."""
PLANT_ROOM_C, DEAD_LEG_TAU_S = 26.0, 600.0
"""A buffer tank's outlet sensor sits in its outlet pipe: with no flow through the tank the
water round it stands, and warms toward the plant room with this time constant."""
TANK_PRESSURE_KPA, PRIMARY_HEAD_KPA = 150.0, 120.0

CELL_KW, CELL_UA = 1000.0, 1000.0 / 7.5
"""A cell rejects CELL_KW at 27 °C wet bulb with CW 32 / 37 °C and its fan flat out."""
NATURAL_DRAFT = 0.05
"""Share of a cell's air flow with its fan stopped."""
STANDING_UA = 0.3 * CELL_UA
"""Heat a cell's standing basin exchanges with the air per kelvin above the wet bulb: with no
condenser water flowing, it settles to the wet bulb within minutes."""
FAN_KW, FAN_MIN_PCT, FAN_PCT_PER_S = 3.0, 20.0, 5.0
FAN_KP, FAN_KI = 10.0, 0.1
BASIN_KG = 20_000.0
CELL_VOLTS, CELL_PF = 400.0, 0.86

ETA = 0.845
"""A chiller's share of the Carnot COP at its best part load."""
IDLE_POWER = 0.03
"""Share of rated power an unloaded compressor draws."""
Q_TAU_S = 20.0
"""Capacity control: how fast the chiller moves towards the load it is asked for."""
HP_LIMIT_KPA, HP_TRIP_KPA, LP_TRIP_KPA = 1200.0, 1350.0, 300.0
"""Condenser pressure above which the chiller unloads and at which it trips, and the
evaporator pressure (freeze protection) at which it trips."""
TRIP_RESET_S = 900
"""A safety trip resets itself this long after its cause has gone, or at an operator Reset."""

FLOW_PROVE_S, SOFT_LOAD_S, PUMP_RUNON_S, MIN_RUN_S = 10, 180, 60, 300
STAGE_UP_WAIT_S, STAGE_DOWN_WAIT_S, STAGE_UP_INHIBIT_S = 180, 300, 300
STAGE_DOWN_MARGIN = 0.9
"""Stage down only once the demand fits in one chiller fewer with this margin."""
CHWS_HIGH_K = 1.0
"""Supply this far above setpoint calls for another chiller, whatever the demand says."""
TIMER_CAP_S = 3600
DRIFT_LIMIT_C = 8.0
"""How far a drifting chilled-water sensor can read high before it saturates."""
"""Timers stop counting here, so worlds that took different paths converge."""

FRESH_AIR_KG_S = 2.0
"""Outdoor air each primary air handler brings in and dehumidifies on its chilled-water coil."""
OFF_COIL_C = 12.5
"""Air leaving a fresh-air coil, nearly saturated."""

INITIAL_LEAD = "CH-001"
INITIAL_RUN_HOURS = {"CH-001": 3000.0, "CH-002": 2068.0, "CH-003": 1984.0, "CH-004": 1876.0}
INITIAL_TOWER_HOURS = {"CT-001": 1937.0, "CT-002": 1863.0, "CT-003": 1789.0, "CT-004": 1715.0}
INITIAL_ROTATION = ("2026-08-27 14:27:02", "2026-08-27|Thursday|14:27", 1)
"""(last rotation, its schedule key, rotations so far) as the plant was handed over."""
DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
ROTATION_SCHEDULE = {
    "Monday": (True, "02:00"),
    "Tuesday": (False, "02:00"),
    "Wednesday": (True, "02:00"),
    "Thursday": (True, "14:27"),
    "Friday": (True, "02:00"),
    "Saturday": (False, "04:00"),
    "Sunday": (False, "02:00"),
}
"""Weekday → (rotation enabled, local time)."""

OFF, OPENING, PUMPS, SOFT, RUNNING, RUNON, CLOSING = (
    "OFF",
    "OPENING VALVES",
    "STARTING PUMPS",
    "SOFT LOADING",
    "RUNNING",
    "PUMP RUN-ON",
    "CLOSING VALVES",
)
"""Steps of a chiller's start/stop sequence."""
_COUNTS = frozenset({"min_chillers", "max_chillers"})
"""Controller settings that count chillers: a commanded value rounds to a whole number."""
_COMPRESSOR = frozenset({SOFT, RUNNING})
_PUMPS = frozenset({PUMPS, SOFT, RUNNING, RUNON})
_STARTING = frozenset({OPENING, PUMPS, SOFT})


def _number(
    name: str, label: str, lo: float, hi: float, unit: str = "", variable: str = ""
) -> CommandSpec:
    """A bounded numeric setting of the plant Controller."""
    return CommandSpec(name, label, variable or name, "number", minimum=lo, maximum=hi, unit=unit)


@dataclass(frozen=True, slots=True)
class Leg:
    name: str
    """`CH-001`…`CH-004`."""
    chiller: str
    chw_pump: str
    cw_pump: str
    evap_valve: str
    cond_valve: str
    header_valve: str
    tanks: tuple[str, ...]
    cells: tuple[str, ...]
    tower: str
    """Its Tower Group, `CT-001`…`CT-004`."""

    @property
    def valves(self) -> tuple[str, str, str]:
        return (self.evap_valve, self.cond_valve, self.header_valve)


@dataclass(frozen=True, slots=True)
class Layout:
    legs: tuple[Leg, ...]
    secondary: str
    bypass: tuple[str, ...]
    loads: tuple[tuple[str, float, tuple[tuple[str, float], ...]], ...]
    """(zone, its UA, (chilled-water air supplier, its share)) for every zone."""
    fresh_air: tuple[str, ...]
    """The fresh-air handlers, whose coils also dry the outdoor air they bring in."""
    units: tuple[str, ...]
    """Every chilled-water air unit, whose fan heat its coil also takes up."""
    zone_ua: tuple[tuple[str, float], ...]
    """(zone, its UA), in the order of `loads`."""
    demand_plan: tuple[tuple[str, int, float, bool], ...]
    """(chilled-water air unit, index of the zone it serves in `zone_ua`, its share of that
    zone's cooling, whether it brings in fresh air), each unit once."""

    @property
    def pumps(self) -> tuple[str, ...]:
        return (*(p for leg in self.legs for p in (leg.chw_pump, leg.cw_pump)), self.secondary)

    @property
    def valves(self) -> tuple[str, ...]:
        return (*(v for leg in self.legs for v in leg.valves), *self.bypass)

    def leg(self, name: str) -> Leg:
        return next(leg for leg in self.legs if leg.name == name)


@functools.cache
def plant_layout(design: PlantDesign) -> Layout:
    """The plant as the Plant Design connects it."""

    def of(nodes, type_id: str) -> list[str]:
        return [n for n in nodes if _type(design, n) == type_id]

    chillers = [a.path for a in design.assets.values() if a.type_id == CHILLER_TYPE and a.room]
    legs, on_legs = [], set()
    for chiller in chillers:
        chw_up = design.upstream(chiller, ConnectionKind.CHW)
        cw_up = design.upstream(chiller, ConnectionKind.CW)
        (header,) = of(design.downstream(chiller, ConnectionKind.CHW), VALVE_TYPE)
        cells = tuple(of(design.downstream(chiller, ConnectionKind.CW), TOWER_TYPE))
        leg = Leg(
            _tag(design, chiller, "CH"),
            chiller,
            of(chw_up, PUMP_TYPE)[0],
            of(cw_up, PUMP_TYPE)[0],
            of(chw_up, VALVE_TYPE)[0],
            of(cw_up, VALVE_TYPE)[0],
            header,
            tuple(of(design.downstream(header, ConnectionKind.CHW), TANK_TYPE)),
            cells,
            f"CT-{_tag(design, chiller, 'CH')[3:]}",
        )
        legs.append(leg)
        on_legs.update((leg.chw_pump, leg.cw_pump, *leg.valves))
    legs.sort(key=lambda leg: leg.name)
    (secondary,) = {
        p for leg in legs for p in of(design.downstream(leg.header_valve, "chw"), PUMP_TYPE)
    }
    bypass = sorted(
        (
            a.path
            for a in design.assets.values()
            if a.type_id == VALVE_TYPE and a.room and a.path not in on_legs
        ),
        key=lambda v: _tag(design, v, "BV"),
    )
    loads, units = [], []
    for zone in zones(design):
        chw = tuple((n, s) for n, s in air_suppliers(design, zone) if _type(design, n) != CRAC_TYPE)
        loads.append((zone, ua_kw_per_k(design, zone), chw))
        units.extend(n for n, _ in chw)
    units = tuple(dict.fromkeys(units))
    fresh_air = tuple(u for u in units if _type(design, u) == FRESH_AIR_TYPE)
    return Layout(
        tuple(legs),
        secondary,
        tuple(bypass),
        tuple(loads),
        fresh_air,
        units,
        tuple((zone, ua) for zone, ua, _ in loads),
        tuple(
            (unit, i, share, unit in fresh_air)
            for i, (_, _, suppliers) in enumerate(loads)
            for unit, share in suppliers
        ),
    )


@functools.cache
def _pump_ratings(design: PlantDesign) -> dict[str, tuple[float, float]]:
    """Plant pump → (rated power, design drive frequency)."""
    layout = plant_layout(design)
    ratings = {layout.secondary: (SECONDARY_PUMP_KW, 50.0)}
    for leg in layout.legs:
        ratings[leg.chw_pump] = (CHW_PUMP_KW, CHW_HZ)
        ratings[leg.cw_pump] = (CW_PUMP_KW, CW_HZ)
    return ratings


@functools.cache
def plant_nodes(design: PlantDesign) -> tuple[str, ...]:
    """The consumers whose power the plant works out: chillers, pumps and tower cells."""
    layout = plant_layout(design)
    return (
        *(leg.chiller for leg in layout.legs),
        *layout.pumps,
        *(c for leg in layout.legs for c in leg.cells),
    )


def _type(design: PlantDesign, node: str) -> str:
    placed = design.assets.get(node)
    return "" if placed is None else placed.type_id


def _tag(design: PlantDesign, node: str, prefix: str) -> str:
    """The plant tag (`CH-004`, `BV-002`) an asset's authored role names."""
    found = re.search(rf"\b{prefix}-\d{{3}}\b", design.asset(node).role)
    if found is None:
        raise ValueError(f"{node} has no {prefix} tag in its role")
    return found.group()


# ---- Refrigerant and chiller physics


def saturation_kpa(t_c: float) -> float:
    """R-134a saturation pressure (absolute), within 1 % from 0 to 50 °C."""
    return math.exp(15.392 - 2651.5 / (t_c + 273.15))


def _cycle(q: float, chw_in: float, chw_lps: float, cw_in: float, cw_lps: float, c) -> tuple:
    """(leaving CHW, leaving CW, evaporating, condensing, power) of a chiller removing `q`."""
    plr = q / CHILLER_KWR
    fouling = c.get("constraint.condenser_fouling", 0.0) if c else 0.0
    wear = c.get("constraint.compressor_degradation", 0.0) if c else 0.0
    leaving = chw_in - q / (CP * chw_lps) if chw_lps > 0.0 else chw_in
    evaporating = leaving - (0.5 + 2.0 * plr)
    power = q / 6.0
    cw_out = condensing = cw_in
    for _ in range(2):  # the heat rejected depends on the power; this settles it
        cw_out = cw_in + (q + power) / (CP * cw_lps) if cw_lps > 0.0 else cw_in
        condensing = cw_out + (0.8 + 2.0 * plr) * (1.0 + 3.0 * fouling)
        lift = max(condensing - evaporating, 5.0)
        eta = ETA * (1.0 - 0.5 * (min(plr, 1.0) - 0.75) ** 2) * (1.0 - 0.3 * wear)
        power = q * lift / (eta * (evaporating + 273.15)) + IDLE_POWER * RATED_KW
    return leaving, cw_out, evaporating, condensing, power


RATED_KW = 0.0
RATED_KW = _cycle(CHILLER_KWR, CHWS_SP_C + CHW_DT_K, LEG_CHW_LPS, 32.0, LEG_CW_LPS, None)[4]
"""Input power at full load at design conditions; compressor current is a share of it."""


# ---- The domain


class ChillerPlantDomain:
    """The chiller plant, its buffer tanks and Tower Groups, and the plant Controllers. It
    steps before the site loads and the electrical network, reading the zones' air as the
    previous step left it."""

    settling_s = 3600
    """The tank layers flush, the basins settle, and a swapped chiller set hands back."""
    commands = {
        CHILLER_TYPE: (
            CommandSpec("mode", "Hand / auto", "mode", choices=("auto", "hand")),
            CommandSpec("run", "Start / stop (hand)", "hand_run", kind="switch"),
            CommandSpec("enable", "Enabled", "enabled", kind="switch"),
            CommandSpec("reset", "Reset trip", "reset", kind="switch"),
        ),
        CONTROLLER_TYPE: (
            _number("min_chillers", "Minimum chillers", 0, 4),
            _number("max_chillers", "Maximum chillers", 1, 4),
            _number("load_limit", "Chiller load limit", 40, 100, "%", "load_limit_pct"),
            _number("chws_sp", "CHWS temperature setpoint", 8, 18, "°C", "chws_sp_c"),
            _number("cws_sp", "CWS temperature setpoint", 20, 35, "°C", "cws_sp_c"),
            CommandSpec("dp_mode", "DP PID mode", "dp_mode", choices=("AUTO", "MANUAL")),
            _number("dp_sp", "DP setpoint", MIN_DP_KPA, 200, "kPa", "dp_sp_kpa"),
            _number("dp_manual", "DP PID manual output", 0, 100, "%", "dp_manual_pct"),
            CommandSpec("bp_mode", "Bypass PID mode", "bp_mode", choices=("AUTO", "MANUAL")),
            _number("bp_sp", "Bypass DP setpoint", 20, 150, "kPa", "bp_sp_kpa"),
            _number("bp_manual", "Bypass PID manual output", 0, 100, "%", "bp_manual_pct"),
        ),
        TOWER_TYPE: HAND_AUTO,
        TANK_TYPE: TANK_COMMANDS,
    }

    def initial(self, ctx: StepContext) -> dict[str, AssetState]:
        layout = plant_layout(ctx.design)
        states: dict[str, AssetState] = {PLANT: _plant_initial(ctx)}
        for leg in layout.legs:
            states[leg.chiller] = {
                "mode": "auto",
                "hand_run": False,
                "enabled": True,
                "reset": False,
                "run_cmd": False,
                "seq": OFF,
                "seq_s": TIMER_CAP_S,
                "prove_s": 0.0,
                "ramp": 0.0,
                "running": False,
                "cycling": False,
                "trip": "",
                "trip_s": 0,
                "cooling_kw": 0.0,
                "drift_c": 0.0,
                "chws_read_c": CHWS_SP_C,
                "run_s": 0.0,
                "run_h0": INITIAL_RUN_HOURS.get(leg.name, 0.0),
                "starts": 0,
                "starts_day": 0,
                **dict.fromkeys(_CHILLER_OUT, 0.0),
            }
            for tank in leg.tanks:
                states[tank] = {
                    **{f"t{k}": CHWS_SP_C for k in range(1, LAYERS + 1)},
                    "in_c": CHWS_SP_C,
                    "out_c": CHWS_SP_C,
                    "flow_lps": 0.0,
                    "recharge_lps": 0.0,
                    "rc_cmd_pct": 0.0,
                    "rc_pos_pct": 0.0,
                    "mode": 0,
                    "avg_c": CHWS_SP_C,
                    "alarm": False,
                    "pressure_kpa": TANK_PRESSURE_KPA,
                    **TANK_VALVES_INITIAL,
                }
            for cell in leg.cells:
                states[cell] = {
                    "running": False,
                    "fan_pct": 0.0,
                    "power_kw": 0.0,
                    "energy_kwh": 0.0,
                    "volts": CELL_VOLTS,
                    "trip": False,
                    **HAND_AUTO_STATE,
                }
        for pump in layout.pumps:
            states[pump] = {"run_cmd": False, "running": False, "hz": 0.0, "flow_lps": 0.0}
            states[pump]["power_kw"] = 0.0
        states[layout.secondary] |= {"run_cmd": True, "cv_pct": 100.0, "speed_pct": 100.0}
        for valve in layout.valves:
            states[valve] = {"cmd_pct": 0.0, "pos_pct": 0.0}
        return states

    def complete(self, state: WorldState, ctx: StepContext) -> None:
        """The plant settled on the demand the zones put on it: the chillers the sequencer
        would run, every Controller at its steady output and every water volume at the
        temperature it holds."""
        design, a = ctx.design, state.assets
        layout, p = plant_layout(design), state.assets[PLANT]
        wb = site_air(state, design)["wet_bulb_c"]
        demand, taken = _demand(state, layout, p)
        p["demand_kw"] = demand
        cap = CHILLER_KWR * p["load_limit_pct"] / 100.0
        p["required"] = _bounded(math.ceil(demand / cap), p)
        order = [layout.leg(n) for n in _priority(layout, p["lead"])]
        on = [leg for leg in order if _available(state, design, leg)][: p["required"]]
        qp = LEG_CHW_LPS * len(on)
        qs_dem = max(demand / (CP * CHW_DT_K), MIN_FLOW_LPS)
        kv = qs_dem / math.sqrt(p["dp_sp_kpa"])
        speed = math.sqrt(p["dp_sp_kpa"] * (1.0 + PIPE_R * kv * kv) / SECONDARY_HEAD_KPA)
        speed_pct = min(max(100.0 * speed, PUMP_MIN_SPEED_PCT), 100.0)
        qs = kv * math.sqrt(SECONDARY_HEAD_KPA * (speed_pct / 100.0) ** 2 / (1.0 + PIPE_R * kv**2))
        qd = qp - qs
        t_s = p["chws_sp_c"]
        load = demand * min(qs / qs_dem, 1.0)
        rise = load / (CP * qs) if qs > 0.0 else 0.0
        t_r = (qs * (t_s + rise) + max(qd, 0.0) * t_s) / (qs + max(qd, 0.0)) if qs else t_s
        pos = max(qd, 0.0) * (BYPASS_SHUTOFF_KPA / p["bp_sp_kpa"] - 1.0) / BYPASS_CV_LPS
        pos_pct = min(max(100.0 * pos, 0.0), 100.0)
        sec = a[layout.secondary]
        sec |= {"run_cmd": True, "running": True, "cv_pct": speed_pct, "speed_pct": speed_pct}
        sec["hz"] = 0.5 * speed_pct
        sec["flow_lps"] = qs
        p |= {"dp_i": speed_pct, "dp_kpa": p["dp_sp_kpa"] if qs_dem > 0 else 0.0}
        p |= {"bp_i": pos_pct, "bp_kpa": p["bp_sp_kpa"] if qd > 0.0 else 0.0}
        p |= {"chws_c": t_s, "chwr_c": t_r, "t_ret": t_r, "delivery": min(qs / qs_dem, 1.0)}
        p |= {"flow_lps": qs, "flow_avg": qs, "dp_avg": p["dp_kpa"]}
        p |= {"up_s": 0.0, "down_s": 0.0, "inhibit_s": float(p["stage_up_inhibit_s"])}
        for v in layout.bypass:
            a[v] |= {"cmd_pct": pos_pct, "pos_pct": pos_pct}
        for leg in layout.legs:
            running = leg in on
            c = a[leg.chiller]
            c |= {"run_cmd": running, "seq": RUNNING if running else OFF, "running": running}
            c |= {"ramp": 1.0 if running else 0.0, "seq_s": TIMER_CAP_S, "prove_s": 0.0}
            pct = 100.0 if running else 0.0
            for v in leg.valves:
                a[v] |= {"cmd_pct": pct, "pos_pct": pct}
            for pump, hz, lps in (
                (leg.chw_pump, CHW_HZ, LEG_CHW_LPS),
                (leg.cw_pump, CW_HZ, LEG_CW_LPS),
            ):
                a[pump] |= {"run_cmd": running, "running": running, "hz": hz if running else 0.0}
                a[pump]["flow_lps"] = lps if running else 0.0
            q = LEG_CHW_LPS * CP * (t_r - t_s) if running else 0.0
            c["cooling_kw"] = q
            # Tower Group: the fan speed that holds the basin at setpoint, if it can.
            sp = max(p["cws_sp_c"], wb + p["approach_sp_k"])
            basin, fan = (sp, FAN_MIN_PCT) if running else (wb, 0.0)
            if running:
                for _ in range(4):
                    _, cw_out, *_, power = _cycle(q, t_r, LEG_CHW_LPS, basin, LEG_CW_LPS, c)
                    rejected = q + power
                    needed = rejected / ((basin + cw_out) / 2.0 - wb) if basin > wb else math.inf
                    fan = _fan_for(needed / (CELL_UA * len(leg.cells)))
                    ua = _tower_ua(a, leg, fan)
                    if fan in (FAN_MIN_PCT, 100.0):
                        rise_cw = rejected / (CP * LEG_CW_LPS)
                        basin = wb + rejected / ua - rise_cw / 2.0
                    else:
                        basin = sp
            p[f"{leg.tower}.basin_c"] = basin
            p[f"{leg.tower}.fan_i"] = fan if running else FAN_MIN_PCT
            for cell in leg.cells:
                a[cell] |= {"fan_pct": fan, "running": running}
            for tank in leg.tanks:
                t = a[tank]
                t |= {f"t{k}": t_s for k in range(1, LAYERS + 1)}
                t["rc_cmd_pct"] = t["rc_pos_pct"] = 0.0
        self._physics(state, ctx, demand, taken, settle=True)
        _stage(state, design, layout, p, 0.0)

    def handles(self, event: Event, design: PlantDesign) -> bool:
        placed = design.assets.get(event.target)
        return (
            event.kind == "command"
            and placed is not None
            and placed.type_id in self.commands
            and command_problem(self.commands[placed.type_id], event.params) is None
        )

    def apply(self, event: Event, state: WorldState) -> None:
        name, value = event.params["command"], event.params["value"]
        s = state.assets[event.target]
        if event.target == PLANT:
            specs = self.commands[CONTROLLER_TYPE]
        elif "seq" in s:
            specs = self.commands[CHILLER_TYPE]
        elif "fan_pct" in s:
            specs = self.commands[TOWER_TYPE]
        else:
            specs = self.commands[TANK_TYPE]
        spec = next(c for c in specs if c.name == name)
        if spec.variable in _COUNTS:
            value = round(value)
        state.assets[event.target][spec.variable] = value

    def step(self, state: WorldState, ctx: StepContext) -> None:
        design = ctx.design
        layout, p = plant_layout(design), state.assets[PLANT]
        demand, taken = _demand(state, layout, p)
        p["demand_kw"] = demand
        _stage(state, design, layout, p, ctx.dt)
        _rotate(state, design, layout, p, ctx.time)
        for leg in layout.legs:
            _sequence(state, design, leg, p, ctx.dt)
        self._physics(state, ctx, demand, taken, settle=False)

    def _physics(
        self, state: WorldState, ctx: StepContext, demand: float, taken: float, settle: bool
    ) -> None:
        """Move the equipment, work out the flows, and carry the heat, given what the units
        ask of the plant and what their coils take up (`_demand`). With `settle` the water
        volumes and Controllers stay where `complete` put them."""
        design, a, dt = ctx.design, state.assets, ctx.dt
        layout, p = plant_layout(design), state.assets[PLANT]
        wb = site_air(state, design)["wet_bulb_c"]
        flags = _supply_flags(design)

        def live(node: str) -> bool:
            flag = flags[node]
            return flag is None or a.get(flag, _LIVE).get("live", True)

        # Valves and pumps answer their commands.
        if not settle:
            for valve, stroke_s in _strokes(design):
                v = a[valve]
                if v["pos_pct"] != v["cmd_pct"] and v.get("constraint.stuck", 0.0) < 0.5:
                    stroke = 100.0 * dt / stroke_s
                    v["pos_pct"] += min(max(v["cmd_pct"] - v["pos_pct"], -stroke), stroke)
        chw, cw = [], []
        for leg in layout.legs:
            chw_pump, cw_pump = a[leg.chw_pump], a[leg.cw_pump]
            _pump(chw_pump, live(leg.chw_pump), CHW_HZ, dt, settle)
            _pump(cw_pump, live(leg.cw_pump), CW_HZ, dt, settle)
            chw_open = min(a[leg.evap_valve]["pos_pct"], a[leg.header_valve]["pos_pct"]) / 100.0
            chw_pump["flow_lps"] = chw_pump["hz"] / CHW_HZ * LEG_CHW_LPS * chw_open
            cw_pump["flow_lps"] = (
                cw_pump["hz"] / CW_HZ * LEG_CW_LPS * a[leg.cond_valve]["pos_pct"] / 100.0
            )
            chw.append(chw_pump["flow_lps"])
            cw.append(cw_pump["flow_lps"])

        # Secondary loop under the DP PID.
        sec = a[layout.secondary]
        qs_dem = max(demand / (CP * CHW_DT_K), MIN_FLOW_LPS)
        if not settle:
            if p["dp_mode"] == "AUTO":
                e = (p["dp_sp_kpa"] - p["dp_kpa"]) / p["dp_sp_kpa"]
                e *= 1.0 + sec.get("controller.gain_factor", 0.0)  # a mistuned loop
                lo = p["pump_min_pct"]
                p["dp_i"] = min(max(p["dp_i"] + DP_KI * e * dt, lo), 100.0)
                sec["cv_pct"] = min(max(DP_KP * e + p["dp_i"], lo), 100.0)
            else:
                sec["cv_pct"] = p["dp_manual_pct"]
            _pump(sec, live(layout.secondary), 50.0 * sec["cv_pct"] / 100.0, dt, False)
            sec["speed_pct"] = 2.0 * sec["hz"]
        kv = qs_dem / math.sqrt(p["dp_sp_kpa"])
        dp = SECONDARY_HEAD_KPA * (sec["speed_pct"] / 100.0) ** 2 / (1.0 + PIPE_R * kv * kv)
        qs = kv * math.sqrt(dp) if sec["running"] else 0.0
        dp = dp if sec["running"] else 0.0
        sec["flow_lps"] = qs
        delivery = min(qs / qs_dem, 1.0)
        p["dp_kpa"], p["flow_lps"], p["delivery"] = dp, qs, delivery

        # Chillers: capacity control, then the refrigerant cycle.
        t_in, supply = p["t_ret"], 0.0
        for i, leg in enumerate(layout.legs):
            c = a[leg.chiller]
            on = c["seq"] in _COMPRESSOR and chw[i] > 0.0
            if on:
                sp = p["chws_sp_c"]
                cap = CHILLER_KWR * p["load_limit_pct"] / 100.0 * c["ramp"]
                cap *= 1.0 - 0.5 * c.get("constraint.compressor_degradation", 0.0)
                hp = c["cond_kpa"] if not settle else 0.0
                cap *= min(max((HP_TRIP_KPA - hp) / (HP_TRIP_KPA - HP_LIMIT_KPA), 0.7), 1.0)
                target = min(max(chw[i] * CP * (t_in - sp), 0.0), cap)
                if not settle:
                    c["cooling_kw"] += (target - c["cooling_kw"]) * min(dt / Q_TAU_S, 1.0)
                c["cooling_kw"] = min(c["cooling_kw"], cap)
            else:
                c["cooling_kw"] = 0.0
            c["running"] = on
            basin = p[_tower_keys(leg.tower)["basin_c"]]
            if not on and not c["power_kw"] and not chw[i] and not cw[i]:
                # Standing: the refrigerant settles between the water on either side.
                evap_kpa, cond_kpa = saturation_kpa(t_in), saturation_kpa(basin)
                c["chws_c"] = c["chwr_c"] = t_in
                c["cw_in_c"] = c["cw_out_c"] = c["disch_hi_c"] = basin
                c["disch_lo_c"] = (t_in + basin) / 2.0
                c["evap_kpa"], c["cond_kpa"], c["cond_hp_kpa"] = evap_kpa, cond_kpa, cond_kpa
                c["chw_lps"] = c["cw_lps"] = c["approach_c"] = c["cop"] = 0.0
                c["load_pct"] = c["current_pct"] = 0.0
                continue
            if on:
                leaving, cw_out, evap, cond, power = _cycle(
                    c["cooling_kw"], t_in, chw[i], basin, cw[i], c
                )
            else:  # the refrigerant settles between the water on either side
                leaving, cw_out, power, evap, cond = t_in, basin, 0.0, t_in, basin
            q = c["cooling_kw"]
            plr = q / CHILLER_KWR
            c |= {
                "chws_c": leaving,
                "chwr_c": t_in,
                "chw_lps": chw[i],
                "cw_lps": cw[i],
                "cw_in_c": basin,
                "cw_out_c": cw_out,
                "evap_kpa": saturation_kpa(evap),
                "cond_kpa": saturation_kpa(cond),
                "disch_hi_c": cond + (8.0 + 12.0 * plr if on else 0.0),
                "disch_lo_c": (evap + cond) / 2.0 + (4.0 + 6.0 * plr if on else 0.0),
                "approach_c": leaving - evap,
                "power_kw": power if live(leg.chiller) else 0.0,
                "cop": q / power if power > 0.0 else 0.0,
                "load_pct": 100.0 * plr,
                "current_pct": 100.0 * power / RATED_KW,
            }
            c["cond_hp_kpa"] = 1.03 * c["cond_kpa"] if on else c["cond_kpa"]
            supply += q

        # Buffer tanks: in line on a running leg, recharged from the supply header on a
        # standby one.
        qp = sum(chw)
        t_s = p["chws_c"]
        qp_heat, recharge, recharge_heat = 0.0, 0.0, 0.0
        warm_tanks = [False] * len(layout.legs)
        fan_trips = [False] * len(layout.legs)
        for i, leg in enumerate(layout.legs):
            leaving = a[leg.chiller]["chws_c"]
            head = TANK_PRESSURE_KPA + PRIMARY_HEAD_KPA * (a[leg.chw_pump]["hz"] / CHW_HZ) ** 2
            inline = chw[i] / len(leg.tanks)
            enabled = a[leg.chiller]["enabled"]
            for tank in leg.tanks:
                t = a[tank]
                through = inline * _tank_valves(t)
                qp_heat += (inline - through) * leaving  # what the bypass valve carries past
                if not settle:
                    # Recharge Controller: a standby tank that has warmed, or has just left
                    # line service, is re-cooled from the supply header until charged.
                    if inline or not enabled:
                        cmd = 0.0
                    elif t["avg_c"] > t_s + RECHARGE_START_K or t["mode"] == 1:
                        cmd = 100.0  # warmed, or just out of line service
                    elif max(t[key] for key in LAYER_KEYS) < t_s + RECHARGE_STOP_K:
                        cmd = 0.0  # charged through every layer
                    else:
                        cmd = t["rc_cmd_pct"]
                    t["rc_cmd_pct"] = cmd
                    if t["rc_pos_pct"] != cmd:
                        stroke = 100.0 * dt / VALVE_STROKE_S
                        t["rc_pos_pct"] += min(max(cmd - t["rc_pos_pct"], -stroke), stroke)
                rc = RECHARGE_LPS * t["rc_pos_pct"] / 100.0 if qp > 0.0 and not inline else 0.0
                rc = rc if t["no_open"] else 0.0  # recharge enters through the inlet valve too
                flow, inlet = (through, leaving) if inline > 0.0 else (rc, t_s)
                if not flow:  # isolated: the charge it holds does not change
                    t["in_c"], t["flow_lps"], t["recharge_lps"], t["mode"] = inlet, 0.0, 0.0, 0
                    # Its outlet pipe stands: the sensor there drifts toward the plant room.
                    gain = 1.0 if settle else min(dt / DEAD_LEG_TAU_S, 1.0)
                    t["out_c"] += (PLANT_ROOM_C - t["out_c"]) * gain
                    t["pressure_kpa"], t["alarm"] = head, False
                    continue
                if not settle:
                    # Each layer takes water from the one below it; the bottom from the inlet.
                    f = min(flow * dt / (TANK_KG / LAYERS), 1.0)
                    t1, t2, t3, t4, t5, t6 = t["t1"], t["t2"], t["t3"], t["t4"], t["t5"], t["t6"]
                    t["t1"] = t1 + (t2 - t1) * f
                    t["t2"] = t2 + (t3 - t2) * f
                    t["t3"] = t3 + (t4 - t3) * f
                    t["t4"] = t4 + (t5 - t4) * f
                    t["t5"] = t5 + (t6 - t5) * f
                    t["t6"] = t6 + (inlet - t6) * f
                avg = (t["t1"] + t["t2"] + t["t3"] + t["t4"] + t["t5"] + t["t6"]) / LAYERS
                t["in_c"], t["out_c"], t["flow_lps"], t["recharge_lps"] = inlet, t["t1"], flow, rc
                t["mode"] = 1 if through > 0.0 else 2
                t["avg_c"] = avg
                t["pressure_kpa"] = head
                t["alarm"] = avg > p["chws_sp_c"] + TANK_ALARM_K and flow > 0.0
                warm_tanks[i] = warm_tanks[i] or t["alarm"]
                recharge += rc
                recharge_heat += rc * t["t1"]
                if through > 0.0:
                    qp_heat += through * t["t1"]
        t_p = qp_heat / qp if qp > 0.0 else t_s

        # Headers and the decoupler.
        qd = qp - qs - recharge
        load = taken * delivery
        load_out = t_s + load / (CP * qs) if qs > 0.0 else p["chwr_c"]
        t_r = p["t_ret"]
        if not settle:
            k = dt / HEADER_KG
            if qd >= 0.0:
                t_s += qp * (t_p - t_s) * k
            else:
                t_s += (qp * (t_p - t_s) - qd * (t_r - t_s)) * k
            into_return = qs * (load_out - t_r) + max(qd, 0.0) * (p["chws_c"] - t_r)
            t_r += (into_return + recharge_heat - recharge * t_r) * k
        p |= {"chws_c": t_s, "chwr_c": load_out, "t_ret": t_r, "t_p": t_p}
        p |= {"qp_lps": qp, "qd_lps": qd, "load_kw": load, "supply_kw": supply}

        # Bypass valves under the bypass PID, on the header DP the surplus raises.
        surplus = max(qd, 0.0)
        pos = sum(a[v]["pos_pct"] for v in layout.bypass) / (100.0 * len(layout.bypass))
        bp = BYPASS_SHUTOFF_KPA * surplus / (surplus + BYPASS_CV_LPS * pos) if surplus else 0.0
        if not settle:
            if p["bp_mode"] == "AUTO":
                e = (bp - p["bp_sp_kpa"]) / p["bp_sp_kpa"]
                p["bp_i"] = min(max(p["bp_i"] + BYPASS_KI * e * dt, 0.0), 100.0)
                cv = min(max(BYPASS_KP * e + p["bp_i"], 0.0), 100.0)
            else:
                cv = p["bp_manual_pct"]
            for v in layout.bypass:
                a[v]["cmd_pct"] = cv
            p["bp_kpa"] = bp
            for name, value, tau in (("dp_avg", dp, 60.0), ("flow_avg", qs, 60.0)):
                p[name] += (value - p[name]) * dt / tau
        p["bp_cv_pct"] = a[layout.bypass[0]]["cmd_pct"]

        # Condenser water and the Tower Groups.
        rejected = 0.0
        cw_flow = cws_heat = cwr_heat = 0.0
        for i, leg in enumerate(layout.legs):
            c = a[leg.chiller]
            k = _tower_keys(leg.tower)
            basin, cw_out = p[k["basin_c"]], c["cw_out_c"]
            flowing = cw[i] > 0.0
            sp = max(p["cws_sp_c"], wb + p["approach_sp_k"])
            if not settle:
                if flowing:
                    e = basin - sp
                    p[k["fan_i"]] = min(max(p[k["fan_i"]] + FAN_KI * e * dt, FAN_MIN_PCT), 100.0)
                    cmd = min(max(FAN_KP * e + p[k["fan_i"]], FAN_MIN_PCT), 100.0)
                else:  # the Controller stands by with its output at minimum
                    cmd = 0.0
                    p[k["fan_i"]] = FAN_MIN_PCT
            step = FAN_PCT_PER_S * dt
            fan_sum = fan_kw = ua = 0.0
            spinning = False
            hands = any(a[x]["mode"] == "hand" and a[x]["hand_run"] for x in leg.cells)
            if not flowing and not p[k["fan_pct"]] and not hands:
                # A standing group: its fans stay stopped, and only their supply and their
                # fan trips (held while the fault that caused them acts) can change.
                for cell in leg.cells:
                    s = a[cell]
                    volts = CELL_VOLTS if live(cell) else 0.0
                    trip = _fan_loss(a, leg, cell) >= FAN_TRIP_LOSS
                    if s["volts"] != volts or s["trip"] != trip:
                        s["volts"], s["trip"] = volts, trip
                    fan_trips[i] = fan_trips[i] or trip
                cells = ()
            else:
                cells = leg.cells
            for cell in cells:
                s = a[cell]
                powered_cell = live(cell)
                loss = _fan_loss(a, leg, cell)
                if not settle:
                    # In hand the fan runs flat out or stops, as the operator switches it.
                    want = (100.0 if s["hand_run"] else 0.0) if s["mode"] == "hand" else cmd
                    if powered_cell and loss < FAN_TRIP_LOSS and want > 0.0:
                        s["fan_pct"] += min(max(want - s["fan_pct"], -step), step)
                    else:
                        s["fan_pct"] = 0.0
                fan = s["fan_pct"]
                s["running"] = fan > 0.0
                s["trip"] = loss >= FAN_TRIP_LOSS
                fan_trips[i] = fan_trips[i] or s["trip"]
                s["power_kw"] = FAN_KW * (fan / 100.0) ** 3 if powered_cell else 0.0
                s["volts"] = CELL_VOLTS if powered_cell else 0.0
                if not settle:
                    s["energy_kwh"] += s["power_kw"] * dt / 3600.0
                fan_sum += fan
                fan_kw += s["power_kw"]
                spinning = spinning or fan > 0.0
                if flowing:
                    air = NATURAL_DRAFT + (1.0 - NATURAL_DRAFT) * fan / 100.0 * (1.0 - loss)
                    fouling = s.get("constraint.fill_fouling", 0.0)
                    ua += CELL_UA * air**0.8 * (1.0 - 0.5 * fouling)
            if not flowing:
                ua = STANDING_UA * len(leg.cells)
            mean = (basin + cw_out) / 2.0 if flowing else basin
            q_tower = ua * (mean - wb)
            if not settle:
                heat = c["cooling_kw"] + c["power_kw"] if flowing else 0.0
                basin += (heat - q_tower) * dt / (CP * BASIN_KG)
                if spinning:
                    p[k["run_s"]] += dt
            p[k["basin_c"]] = basin
            p[k["cwr_c"]] = cw_out if flowing else basin
            p[k["rejected_kw"]] = max(q_tower, 0.0)
            p[k["fan_pct"]] = fan_sum / len(leg.cells)
            p[k["power_kw"]] = fan_kw
            rejected += max(q_tower, 0.0)
            cw_flow += cw[i]
            cws_heat += cw[i] * basin
            cwr_heat += cw[i] * p[k["cwr_c"]]
        p["cw_lps"] = cw_flow
        basins = [p[_tower_keys(leg.tower)["basin_c"]] for leg in layout.legs]
        mean_basin = sum(basins) / len(basins)
        p["cws_c"] = cws_heat / cw_flow if cw_flow else mean_basin
        p["cwr_c"] = cwr_heat / cw_flow if cw_flow else mean_basin
        p["rejected_kw"] = rejected

        # Pump power, and what the plant draws.
        for pump, (rated, design_hz) in _pump_ratings(design).items():
            s = a[pump]
            wear = 1.0 + 0.3 * s.get("constraint.bearing_wear", 0.0)
            s["power_kw"] = rated * (s["hz"] / design_hz) ** 3 * wear if s["running"] else 0.0
        chillers_kw = sum(a[leg.chiller]["power_kw"] for leg in layout.legs)
        pumps_kw = sum(a[pump]["power_kw"] for pump in layout.pumps)
        towers_kw = sum(p[_tower_keys(leg.tower)["power_kw"]] for leg in layout.legs)
        p["plant_kw"] = chillers_kw + pumps_kw + towers_kw
        p["chillers_kw"] = chillers_kw
        p["cop"] = supply / chillers_kw if chillers_kw > 0.0 else 0.0

        # Device logic: safety trips, run hours, and the alarm summary.
        local = ctx.time + LOCAL_OFFSET_S
        for leg in layout.legs:
            c = a[leg.chiller]
            if c["running"]:
                cause = ""
                if c["cond_kpa"] >= HP_TRIP_KPA:
                    cause = "HIGH CONDENSER PRESSURE"
                elif c["evap_kpa"] <= LP_TRIP_KPA:
                    cause = "LOW EVAPORATOR PRESSURE"
                elif c.get("constraint.trip", 0.0) >= 0.5:
                    cause = "COMPRESSOR FAULT"
                if cause and not settle:
                    c |= {"trip": cause, "trip_s": 0, "running": False, "cooling_kw": 0.0}
                    c["seq"], c["seq_s"] = RUNON, 0
            elif c["trip"] and not settle:
                healthy = c["cond_kpa"] < HP_LIMIT_KPA and c.get("constraint.trip", 0.0) < 0.5
                c["trip_s"] = min(c["trip_s"] + int(dt), TIMER_CAP_S) if healthy else 0
                if c["reset"] or c["trip_s"] >= TRIP_RESET_S:
                    c["trip"], c["trip_s"] = "", 0
            c["reset"] = False
            rate = c.get("observation.drift_c_per_h", 0.0)
            if rate or c["drift_c"]:  # the leaving-water sensor drifts; Clear recalibrates
                drifted = c["drift_c"] + rate * dt / 3600.0 if rate > 0.0 else 0.0
                c["drift_c"] = min(drifted, DRIFT_LIMIT_C)
            c["chws_read_c"] = c["chws_c"] + c["drift_c"]
            if c["running"] and not settle:
                c["run_s"] += dt
            if not settle and local % DAY_S == 0:
                c["starts_day"] = 0
        _alarms(state, layout, p, fan_trips, warm_tanks)


@functools.cache
def _supply_flags(design: PlantDesign) -> dict[str, str | None]:
    """Plant equipment → the node whose `live` says whether it has supply (`powered`)."""
    flags = network(design).supply_flag
    return {n: flags.get(n) for n in plant_nodes(design)}


@functools.cache
def _tower_keys(tower: str) -> dict[str, str]:
    """A Tower Group's variables on the plant's AssetState, by name."""
    names = ("basin_c", "cwr_c", "fan_i", "fan_pct", "power_kw", "rejected_kw")
    return {n: f"{tower}.{n}" for n in (*names, "run_s", "run_h0")}


def _tank_valves(t: AssetState) -> float:
    """A buffer tank's inlet (normally open) and bypass (normally closed) valves: move them,
    and return the share of the leg's flow that goes through the tank.

    In auto the tank Controller holds the inlet open and the bypass shut, and opens the bypass
    if the inlet fails to open, so the leg keeps its flow round an isolated tank. In hand the
    operator bypasses the tank or puts it back in line. A seized inlet stays shut; a seized
    bypass stays open, and then half the flow goes round the tank."""
    if t["valve_mode"] == "hand":
        no_cmd, nc_cmd = not t["hand_bypass"], t["hand_bypass"]
    else:
        no_cmd, nc_cmd = True, t["no_fail"]
    no_open = no_cmd and t.get("constraint.inlet_stuck", 0.0) < 0.5
    nc_open = nc_cmd or t.get("constraint.bypass_stuck", 0.0) >= 0.5
    t["no_cmd"], t["nc_cmd"], t["no_open"], t["nc_open"] = no_cmd, nc_cmd, no_open, nc_open
    t["no_fail"], t["nc_fail"] = no_cmd and not no_open, not nc_cmd and nc_open
    if not no_open:
        return 0.0
    return 0.5 if nc_open else 1.0


def _pump(s: AssetState, live: bool, hz: float, dt: float, settle: bool) -> None:
    """A pump answers its run command, its drive ramping to `hz`; it stops without supply
    or once tripped."""
    ok = s["run_cmd"] and live and s.get("constraint.trip", 0.0) < 0.5
    if settle:
        return
    if not ok:
        s["hz"], s["running"] = 0.0, False
        return
    step = PUMP_HZ_PER_S * dt
    s["hz"] += min(max(hz - s["hz"], -step), step)
    s["running"] = s["hz"] > 0.0


@functools.cache
def _strokes(design: PlantDesign) -> tuple[tuple[str, float], ...]:
    """(valve, its full-stroke time) for every plant valve."""
    layout = plant_layout(design)
    return tuple(
        (v, BYPASS_STROKE_S if v in layout.bypass else VALVE_STROKE_S) for v in layout.valves
    )


def _fan_for(ua_share: float) -> float:
    """Fan speed (%) that gives a group `ua_share` of its full-fan UA."""
    flow = max(ua_share, 0.0) ** (1.0 / 0.8)
    return min(max(100.0 * (flow - NATURAL_DRAFT) / (1.0 - NATURAL_DRAFT), FAN_MIN_PCT), 100.0)


def _tower_ua(a: dict, leg: Leg, fan_pct: float | None) -> float:
    """UA of a Tower Group with water on its fill, each cell at its own fan speed (or all at
    `fan_pct`)."""
    ua = 0.0
    for cell in leg.cells:
        s = a[cell]
        speed = (s["fan_pct"] if fan_pct is None else fan_pct) / 100.0
        speed *= 1.0 - _fan_loss(a, leg, cell)
        air = NATURAL_DRAFT + (1.0 - NATURAL_DRAFT) * speed
        ua += CELL_UA * air**0.8 * (1.0 - 0.5 * s.get("constraint.fill_fouling", 0.0))
    return ua


def _fan_loss(a: dict, leg: Leg, cell: str) -> float:
    """The airflow a cell's fan has lost: to its own fault, to its Tower Group's fan supply
    failing, or all of it while its basin is at the low-level trip (`sim.water`)."""
    s = a[cell]
    if s.get("basin_low"):
        return 1.0
    group = max(a[c].get("constraint.group_fan_loss", 0.0) for c in leg.cells)
    return max(s.get("constraint.fan_loss", 0.0), group)


def _tower_failed(a: dict, leg: Leg) -> bool:
    """Whether every cell of the leg's Tower Group has lost its fan: the leg cannot reject
    its chiller's heat, so the sequencer may not start it."""
    return all(_fan_loss(a, leg, cell) >= FAN_TRIP_LOSS for cell in leg.cells)


_LIVE: AssetState = {}
_CHILLER_OUT = (
    "chws_c",
    "chwr_c",
    "chw_lps",
    "cw_lps",
    "cw_in_c",
    "cw_out_c",
    "evap_kpa",
    "cond_kpa",
    "cond_hp_kpa",
    "disch_hi_c",
    "disch_lo_c",
    "approach_c",
    "power_kw",
    "cop",
    "load_pct",
    "current_pct",
)


def _plant_initial(ctx: StepContext) -> AssetState:
    last, key, count = INITIAL_ROTATION
    p: AssetState = {
        "lead": INITIAL_LEAD,
        "min_chillers": 1,
        "max_chillers": 4,
        "required": 1,
        "load_limit_pct": LOAD_LIMIT_PCT,
        "chws_sp_c": CHWS_SP_C,
        "cws_sp_c": CWS_SP_C,
        "approach_sp_k": APPROACH_SP_K,
        "dp_sp_kpa": DP_SP_KPA,
        "min_dp_kpa": MIN_DP_KPA,
        "pump_min_pct": PUMP_MIN_SPEED_PCT,
        "dp_mode": "AUTO",
        "dp_manual_pct": 100.0,
        "bp_sp_kpa": BYPASS_SP_KPA,
        "bp_mode": "AUTO",
        "bp_manual_pct": 35.0,
        "stage_up_wait_s": STAGE_UP_WAIT_S,
        "stage_down_wait_s": STAGE_DOWN_WAIT_S,
        "stage_up_inhibit_s": STAGE_UP_INHIBIT_S,
        "pending": False,
        "last_command": "Scheduled run-hour rotation complete",
        "last_rotation": last,
        "last_key": key,
        "rotations": count,
        "monitor": _next_rotation(ctx.time),
        "next_start": "NONE",
        "next_stop": "NONE",
        "running_count": 0,
        "available_count": 0,
        "alarms": 0,
        "critical": 0,
        "latest_alarm": "",
    }
    for name in (
        "demand_kw",
        "load_kw",
        "supply_kw",
        "chws_c",
        "chwr_c",
        "t_ret",
        "t_p",
        "cws_c",
        "cwr_c",
        "flow_lps",
        "flow_avg",
        "qp_lps",
        "qd_lps",
        "cw_lps",
        "delivery",
        "dp_kpa",
        "dp_avg",
        "dp_i",
        "bp_kpa",
        "bp_i",
        "bp_cv_pct",
        "rejected_kw",
        "plant_kw",
        "chillers_kw",
        "cop",
        "up_s",
        "down_s",
        "inhibit_s",
    ):
        p[name] = 0.0
    p["chws_c"] = p["chwr_c"] = p["t_ret"] = CHWS_SP_C
    p["delivery"] = 1.0
    for tower, hours in INITIAL_TOWER_HOURS.items():
        p |= {f"{tower}.basin_c": CWS_SP_C, f"{tower}.cwr_c": CWS_SP_C, f"{tower}.fan_i": 0.0}
        p |= {f"{tower}.rejected_kw": 0.0}
        p |= {f"{tower}.fan_pct": 0.0, f"{tower}.power_kw": 0.0}
        p |= {f"{tower}.run_s": 0.0, f"{tower}.run_h0": hours}
    return p


def _demand(state: WorldState, layout: Layout, p: AssetState) -> tuple[float, float]:
    """(the cooling the chilled-water air units ask of the plant, what their coils take up
    with full flow of the water the plant supplies now), as each unit the airside models
    reports it, one step behind. For a unit it does not model, the unit asks for what its coil
    would take out at design supply air: the heat it would remove from its zone, the heat of
    its own fan, and for a fresh-air handler the heat and moisture of the outdoor air it
    brings in. Warmer water takes less out of the zones, and a unit without supply asks for
    nothing."""
    a = state.assets
    zones_ = layout.zone_ua
    on = [0.0] * len(zones_)
    fans = 0.0
    handlers = 0
    demand = taken = 0.0
    for unit, zone, share, fresh_air in layout.demand_plan:
        s = a[unit]
        if "chw_demand_kw" in s:  # the airside models the unit's coil (`sim.airside`)
            demand += s["chw_demand_kw"]
            taken += s["chw_take_kw"]
            continue
        kw = s.get("power_kw", 0.0)
        if kw > 0.0:
            fans += kw
            on[zone] += share
            handlers += fresh_air
    coil = p["chws_c"] + COIL_APPROACH_K
    for (zone, ua), share in zip(zones_, on, strict=True):
        if share:
            temp = a[zone]["temp_c"]
            demand += share * ua * max(temp - SUPPLY_C, 0.0)
            taken += share * ua * max(temp - coil, 0.0)
    fresh = 0.0
    air = a.get("~WX-01")
    if air is not None and handlers:
        hpa = air["pressure_hpa"]
        per = FRESH_AIR_KG_S * (
            enthalpy_kj_per_kg(air["dry_bulb_c"], air["dew_point_c"], hpa)
            - enthalpy_kj_per_kg(OFF_COIL_C, OFF_COIL_C, hpa)
        )
        fresh = max(per, 0.0) * handlers
    other = fans + fresh
    return demand + other, taken + other


def _bounded(n: int, p: AssetState) -> int:
    return min(max(n, int(p["min_chillers"])), int(p["max_chillers"]))


def _priority(layout: Layout, lead: str) -> list[str]:
    """Chiller tags in the order the sequencer starts them: the lead, then round from it."""
    names = [leg.name for leg in layout.legs]
    i = names.index(lead) if lead in names else 0
    return names[i:] + names[:i]


def _available(state: WorldState, design: PlantDesign, leg: Leg) -> bool:
    """Whether the sequencer may run this chiller: in auto, enabled, not tripped, and its
    leg able to run: the chiller and both its pumps supplied and untripped, none of its
    valves stuck short of open, and (to start) a fan left in its Tower Group."""
    a = state.assets
    c = a[leg.chiller]
    if hand(c) or not c["enabled"] or c["trip"]:
        return False
    if _tower_failed(a, leg) and not c["running"]:
        return False  # one already running rides on until its own protection trips it
    flags = _supply_flags(design)
    for node in (leg.chiller, leg.chw_pump, leg.cw_pump):
        flag = flags[node]
        if flag is not None and not a.get(flag, _LIVE).get("live", True):
            return False
    for pump in (leg.chw_pump, leg.cw_pump):
        if a[pump].get("constraint.trip", 0.0) >= 0.5:
            return False
    return not any(
        a[v].get("constraint.stuck", 0.0) >= 0.5 and a[v]["pos_pct"] < 95.0 for v in leg.valves
    )


def _stage(state: WorldState, design: PlantDesign, layout: Layout, p: AssetState, dt: float):
    """The sequencer: how many chillers the demand needs, and which of them run."""
    a = state.assets
    legs = {leg.name: leg for leg in layout.legs}
    order = _priority(layout, p["lead"])
    ready = [n for n in order if _available(state, design, legs[n])]
    hands = sum(1 for leg in layout.legs if hand(a[leg.chiller]) and a[leg.chiller]["running"])
    cap = CHILLER_KWR * p["load_limit_pct"] / 100.0
    demand = p["demand_kw"]
    n = _bounded(int(p["required"]), p)
    wanted = _bounded(math.ceil(demand / cap), p)
    running = [n_ for n_ in order if a[legs[n_].chiller]["running"]]
    p["inhibit_s"] = min(p["inhibit_s"] + dt, float(p["stage_up_inhibit_s"]))
    hot = p["chws_c"] > p["chws_sp_c"] + CHWS_HIGH_K and len(running) >= n
    if (wanted > n or hot) and n < p["max_chillers"]:
        p["up_s"] = min(p["up_s"] + dt, TIMER_CAP_S)
        if p["up_s"] >= p["stage_up_wait_s"] and p["inhibit_s"] >= p["stage_up_inhibit_s"]:
            n += 1
            p["up_s"], p["inhibit_s"] = 0.0, 0.0
            p["last_command"] = f"Stage up to {n} chillers"
    else:
        p["up_s"] = 0.0
    low = demand < (n - 1) * cap * STAGE_DOWN_MARGIN and p["chws_c"] <= p["chws_sp_c"] + 0.5
    if low and n > p["min_chillers"]:
        p["down_s"] = min(p["down_s"] + dt, TIMER_CAP_S)
        outgoing = ready[: max(n - hands, 0)][-1:]
        stoppable = all(_ready_to_stop(a[legs[x].chiller]) for x in outgoing)
        if p["down_s"] >= p["stage_down_wait_s"] and stoppable:
            n -= 1
            p["down_s"] = 0.0
            p["last_command"] = f"Stage down to {n} chillers"
    else:
        p["down_s"] = 0.0
    p["required"] = n
    desired = ready[: max(n - hands, 0)]
    starting = any(a[legs[x].chiller]["seq"] != RUNNING for x in desired)
    for name in order:
        c = a[legs[name].chiller]
        keep = starting and name in ready and c["run_cmd"] and c["seq"] == RUNNING
        cmd = name in desired or keep
        if cmd and not c["run_cmd"] and name not in running:
            p["last_command"] = f"Start {name}"
        elif c["run_cmd"] and not cmd:
            p["last_command"] = f"Stop {name}"
        c["run_cmd"] = cmd
    stopping = any(a[leg.chiller]["seq"] in (RUNON, CLOSING) for leg in layout.legs)
    p["pending"] = bool(p["up_s"] or p["down_s"] or starting or stopping)
    spare = [x for x in ready if x not in desired]
    p["next_start"] = spare[0] if spare else "NONE"
    p["next_stop"] = desired[-1] if desired and n > p["min_chillers"] else "NONE"
    p["available_count"] = len(ready) + hands


def hand(c: AssetState) -> bool:
    """Whether a chiller runs in hand: set so by the operator, or its selector left there."""
    return c["mode"] == "hand" or c.get("controller.hand_mode", 0.0) >= 0.5


def run_request(c: AssetState) -> bool:
    """Whether a chiller is asked to run: by the sequencer in auto, by its selector in hand
    (one left in hand is left on)."""
    if not hand(c):
        return c["run_cmd"]
    return (c["hand_run"] or c.get("controller.hand_mode", 0.0) >= 0.5) and c["enabled"]


def _ready_to_stop(c: AssetState) -> bool:
    return c["seq"] == RUNNING and c["seq_s"] >= MIN_RUN_S


def _sequence(state: WorldState, design: PlantDesign, leg: Leg, p: AssetState, dt: float):
    """One chiller's start/stop sequence, and the commands it gives its valves and pumps."""
    a = state.assets
    c = a[leg.chiller]
    flag = _supply_flags(design)[leg.chiller]
    live = flag is None or a.get(flag, _LIVE).get("live", True)
    wants = run_request(c) and not c["trip"]
    seq = c["seq"]
    t = min(c["seq_s"] + dt, TIMER_CAP_S)
    chw_ok = a[leg.chw_pump]["flow_lps"] >= 0.9 * LEG_CHW_LPS
    cw_ok = a[leg.cw_pump]["flow_lps"] >= 0.9 * LEG_CW_LPS
    lost = (
        a[leg.chw_pump]["flow_lps"] < 0.5 * LEG_CHW_LPS
        or a[leg.cw_pump]["flow_lps"] < 0.5 * LEG_CW_LPS
    )

    def go(to: str) -> None:
        nonlocal seq, t
        seq, t = to, 0.0

    if not wants:
        c["cycling"] = False
        if seq in _STARTING and seq != OPENING or seq == RUNNING:
            go(RUNON)
        elif seq == OPENING:
            go(CLOSING)
        elif seq == RUNON and t >= PUMP_RUNON_S:
            go(CLOSING)
        elif seq == CLOSING and all(a[v]["pos_pct"] <= 1.0 for v in leg.valves):
            go(OFF)
    else:
        if seq in (OFF, CLOSING):
            go(OPENING)
        elif seq == RUNON:
            go(PUMPS)
        if seq == OPENING and all(a[v]["pos_pct"] >= 95.0 for v in leg.valves):
            go(PUMPS)
            c["prove_s"] = 0.0
        if seq in _COMPRESSOR and (lost or not live):
            go(PUMPS)
            c["prove_s"] = 0.0
            c["cycling"] = True
        if seq == PUMPS:
            c["prove_s"] = c["prove_s"] + dt if chw_ok and cw_ok else 0.0
            if c["prove_s"] >= FLOW_PROVE_S and live:
                go(SOFT)
                c["ramp"] = c["prove_s"] = 0.0
                c["cycling"] = False
                c["starts"] += 1
                c["starts_day"] += 1
        if seq == SOFT:
            c["ramp"] = min(t / SOFT_LOAD_S, 1.0)
            if c["ramp"] >= 1.0:
                go(RUNNING)
    if seq not in _COMPRESSOR:
        c["ramp"] = 0.0
    c["seq"], c["seq_s"] = seq, t
    open_pct = 0.0 if seq in (OFF, CLOSING) else 100.0
    pumps = seq in _PUMPS
    if a[leg.evap_valve]["cmd_pct"] != open_pct or a[leg.chw_pump]["run_cmd"] != pumps:
        for v in leg.valves:
            a[v]["cmd_pct"] = open_pct
        a[leg.chw_pump]["run_cmd"] = a[leg.cw_pump]["run_cmd"] = pumps


def _rotate(state: WorldState, design: PlantDesign, layout: Layout, p: AssetState, now: int):
    """The rotation schedule: at an enabled day's time, the lead passes to the available
    chiller with the fewest run hours (the BY RUNNING HOURS strategy)."""
    local = now + LOCAL_OFFSET_S
    if local % 60:
        return
    p["monitor"] = _next_rotation(now + 60)
    stamp = time.gmtime(local)
    day = DAYS[stamp.tm_wday]
    enabled, at = ROTATION_SCHEDULE[day]
    if not enabled or time.strftime("%H:%M", stamp) != at:
        return
    a = state.assets
    candidates = [
        leg for leg in layout.legs if leg.name != p["lead"] and _available(state, design, leg)
    ]
    if not candidates:
        return
    lead = min(
        candidates, key=lambda leg: a[leg.chiller]["run_h0"] + a[leg.chiller]["run_s"] / 3600.0
    )
    p["lead"] = lead.name
    p["rotations"] += 1
    p["last_rotation"] = time.strftime("%Y-%m-%d %H:%M:%S", stamp)
    p["last_key"] = f"{time.strftime('%Y-%m-%d', stamp)}|{day}|{at}"
    p["last_command"] = f"Scheduled run-hour rotation: lead {lead.name}"


def _next_rotation(now: int) -> str:
    """`<day> scheduled for HH:MM`: the next enabled rotation at or after `now`."""
    local = now + LOCAL_OFFSET_S
    stamp = time.gmtime(local)
    minute = stamp.tm_hour * 60 + stamp.tm_min
    for ahead in range(8):
        day = DAYS[(stamp.tm_wday + ahead) % 7]
        enabled, at = ROTATION_SCHEDULE[day]
        hh, mm = map(int, at.split(":"))
        if enabled and (ahead or hh * 60 + mm >= minute):
            return f"{day} scheduled for {at}"
    return "NO ROTATION SCHEDULED"


def _alarms(
    state: WorldState, layout: Layout, p: AssetState, fan_trips: list, warm_tanks: list
) -> None:
    """The plant's alarm summary, read from its devices' alarm logic: each chiller's, and
    whether each leg has a tripped tower fan or a warm buffer tank."""
    a = state.assets
    active, critical, latest = 0, 0, ""
    for leg in layout.legs:
        c = a[leg.chiller]
        if c["trip"]:
            active, critical = active + 1, critical + 1
            latest = latest or f"{leg.name} SAFETY TRIP: {c['trip']}"
        elif c["cycling"]:
            active += 1
            latest = latest or f"{leg.name} CYCLING SHUTDOWN"
    for i, leg in enumerate(layout.legs):
        if fan_trips[i]:
            trips = sum(a[cell]["trip"] for cell in leg.cells)
            active, critical = active + trips, critical + trips
            latest = latest or f"{leg.tower} FAN TRIP"
        if warm_tanks[i]:
            active += sum(a[tank]["alarm"] for tank in leg.tanks)
            latest = latest or f"{leg.name} BUFFER TANK HIGH TEMPERATURE"
    p["alarms"], p["critical"], p["latest_alarm"] = active, critical, latest
    p["running_count"] = sum(a[leg.chiller]["running"] for leg in layout.legs)
