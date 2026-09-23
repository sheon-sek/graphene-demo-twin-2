"""The airside (#22): the units that cool each Thermal Zone, and the Cooling Blocks that
carry chilled water to the Data Halls.

DX CRAC units (A4) reject their heat to the outdoor air through their own condensers and do
not depend on chilled water. Their Controller stages two compressors on the load the return
air brings (the lead up to COMPRESSOR1_SHARE of the unit's capacity, then the lag) so the
supply air reaches its setpoint, and speeds the EC fan up once the return air passes its
setpoint. Head pressure rises with the outdoor air, with the load and with a derated
condenser, and above HEAD_LIMIT_C it unloads the compressors.

The chilled-water units are the fresh-air handlers (PAHU), fan-coil (FCU) and fan-wall (FWU)
units and the ceiling cooling units (CCU). Each draws its room's return air over a coil whose
valve its Controller opens to hold supply air at setpoint: the coil can cool the air to
COIL_APPROACH_K above the water the plant supplies, and less as the plant delivers less of
the flow asked of it. The fresh-air handlers also dry the zone's air while their coil has
cold water, and shut down on a fire alarm (the `constraint.fire_alarm` input). The CDUs of
DH08's liquid-cooled pod carry the liquid-cooled share of its IT Load to the chilled water
directly (`removed_kw`); what they cannot carry ends up in the hall air.

Every unit's delivered cooling (`cooling_kw`, and its `airflow` and `supply_c`) feeds the
zone heat balance of the room it serves (`sim.thermal`), and what its coil takes up is the
load it puts on the chiller plant (`chw_demand_kw` asked at setpoint, `chw_take_kw` taken
with full flow of the water supplied now; `sim.plant`). The CRAC domain steps before the
chiller plant; the chilled-water units step after it, on the water it supplied this step,
and the plant reads what they ask of it one step later.
"""

import functools
import math
from dataclasses import dataclass

from graphene_demo_twin.plant_design import ConnectionKind, PlantDesign
from graphene_demo_twin.sim.commands import CommandSpec, command_problem
from graphene_demo_twin.sim.electrical import network
from graphene_demo_twin.sim.engine import StepContext
from graphene_demo_twin.sim.events import Event
from graphene_demo_twin.sim.it_load import it_in
from graphene_demo_twin.sim.plant import (
    CHW_DT_K,
    CHWS_SP_C,
    CP,
    FRESH_AIR_KG_S,
    OFF_COIL_C,
)
from graphene_demo_twin.sim.state import AssetState, WorldState
from graphene_demo_twin.sim.thermal import (
    COIL_APPROACH_K,
    CRAC_TYPE,
    FRESH_AIR_TYPE,
    HALL_C,
    LIQUID_TYPE,
    PLANT,
    SUPPLY_C,
    air_suppliers,
    served_room,
    ua_kw_per_k,
)
from graphene_demo_twin.sim.weather import enthalpy_kj_per_kg, site_air

CCU_TYPE = "Ceiling Cooling Units"
CHW_UNIT_TYPES = frozenset({FRESH_AIR_TYPE, "FCU", "FWU", CCU_TYPE, LIQUID_TYPE})
COOLING_BLOCK_TYPE = "Cooling Block"

# ---- DX CRAC units
COIL_DT_K = 12.0
"""Supply air cooling below return air with both compressors fully loaded at nominal fan."""
COMPRESSOR1_SHARE = 0.7
"""Share of a CRAC's capacity its lead compressor carries before the lag one stages in."""
CRAC_RETURN_SP_C = 26.0
"""Return air above which a CRAC speeds its EC fan up."""
NOMINAL_FAN_PCT, FAN_PCT_PER_K = 80.0, 5.0
"""EC fan speed at or below the return setpoint, and how fast it rises above it."""
CONDENSER_APPROACH_K, CONDENSER_LOAD_K, CONDENSER_DERATE_K = 10.0, 8.0, 25.0
"""Condensing temperature above the outdoor air: unloaded, added at full load, and added by
a fully derated (recirculating, fouled) condenser."""
HEAD_LIMIT_C, HEAD_UNLOAD_K = 55.0, 20.0
"""Condensing temperature above which the compressors unload, and how far above it they
reach the least they may run at (half)."""
HP_ALARM_C = 61.0
"""Condensing temperature at which the head pressure (R-410A, about 4,000 kPa) trips the
high-pressure switch's alarm."""

# ---- Chilled-water units
UNIT_SUPPLY_SP_C = SUPPLY_C
UNIT_RETURN_SP_C, RETURN_RH_SP_PCT, SUPPLY_RH_SP_PCT = 24.0, 50.0, 60.0
"""A fresh-air handler's return air and humidity setpoints."""
VALVE_TAU_S = 20.0
"""How fast a unit's coil valve follows its supply-air Controller."""
STAGNANT_TAU_S = 600.0
"""How fast the water standing in a unit's coil warms towards the room once it stops flowing."""
STATIC_KPA = 0.25
"""A fan unit's static pressure at full airflow through a clean filter."""
DEHUMIDIFY_MAX_CHWS_C = 16.0
"""Warmest supply water on which a fresh-air coil still dries the air."""
FWS_MAX_C = 30.0
"""Facility water at which a CDU can no longer carry any of its pod's heat."""
CDU_PUMPS, CDU_DUTY_PUMPS = 3, 2
"""Pumps in each CDU, and how many run (the third stands by)."""
RISER_GAIN_KW = 2.0
"""Heat a Cooling Block's supply riser picks up on the way to its hall."""


def crac_condensing_c(dry_bulb_c: float, load: float, derate: float) -> float:
    """A CRAC's condensing temperature with its compressors at `load` (0–1) on a condenser
    derated by `derate` (0–1), rejecting to outdoor air at `dry_bulb_c`."""
    return dry_bulb_c + CONDENSER_APPROACH_K + CONDENSER_LOAD_K * load + CONDENSER_DERATE_K * derate


class CracDomain:
    """DX CRAC units and their unit Controller.

    The Controller runs the unit in auto, or follows the operator's start/stop in hand. It
    stages the compressors on the return air to bring supply air to its setpoint, and speeds
    the fan up once the return air passes its setpoint. Equipment response follows the
    Physical Constraints faults put on the unit, and the alarm bits are the unit's own logic.
    """

    SUPPLY_TAU_S = 30.0
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
        load = (HALL_C - SUPPLY_C) / COIL_DT_K
        return {
            crac: {
                "mode": "auto",
                "hand_run": True,
                "setpoint_c": SUPPLY_C,
                "return_sp_c": CRAC_RETURN_SP_C,
                "run_cmd": True,
                "running": True,
                "tripped": False,
                "fan_pct": NOMINAL_FAN_PCT,
                "airflow": 1.0,
                "compressor_pct": 100.0 * load,
                "compressor1_pct": 100.0 * min(load / COMPRESSOR1_SHARE, 1.0),
                "compressor2_pct": 0.0,
                "return_c": HALL_C,
                "supply_c": SUPPLY_C,
                "return_rh_pct": 50.0,
                "supply_rh_pct": 50.0,
                "cooling_kw": 0.0,
                "condensing_c": HEAD_LIMIT_C,
                "alarm_filter": False,
                "alarm_high_pressure": False,
                "alarm_trip": False,
                "alarm_loss_of_signal": False,
                "has_alarm": False,
            }
            for crac in cracs(ctx.design)
        }

    def complete(self, state: WorldState, ctx: StepContext) -> None:
        """Each unit settled on the return air its room starts at."""
        dry = site_air(state, ctx.design)["dry_bulb_c"]
        for crac, room, cap, flag in _crac_plan(ctx.design):
            self._update(state, crac, room, cap, flag, dry, settle=1.0)

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
        dry = site_air(state, ctx.design)["dry_bulb_c"]
        settle = ctx.dt / self.SUPPLY_TAU_S
        for crac, room, cap, flag in _crac_plan(ctx.design):
            self._update(state, crac, room, cap, flag, dry, settle)

    @staticmethod
    def _update(
        state: WorldState,
        crac: str,
        room: str | None,
        cap: float,
        flag: str | None,
        dry: float,
        settle: float,
    ) -> None:
        """Run the unit and its Controller, moving supply air `settle` of the way to what
        the coil leaves."""
        a = state.assets
        s = a[crac]
        if _CRAC_INPUTS.isdisjoint(s):  # healthy, as nearly every unit is
            fan_loss = blockage = trip = derate = offset = 0.0
        else:
            fan_loss = s.get("constraint.fan_loss", 0.0)
            blockage = s.get("constraint.filter_blockage", 0.0)
            trip = s.get("constraint.compressor_trip", 0.0)
            derate = s.get("constraint.condenser_derate", 0.0)
            offset = s.get("controller.setpoint_offset_c", 0.0)
        zone = a.get(room) if room else None
        return_c = zone["temp_c"] if zone is not None else HALL_C

        # Controller: the fan on the return air, the compressors on the load it brings.
        run_cmd = True if s["mode"] == "auto" else s["hand_run"]
        rise = FAN_PCT_PER_K * (return_c - s["return_sp_c"])
        fan = min(max(NOMINAL_FAN_PCT + rise, NOMINAL_FAN_PCT), 100.0)
        coil_dt = COIL_DT_K * NOMINAL_FAN_PCT / fan  # the same capacity over more air
        demand = min(max((return_c - s["setpoint_c"] - offset) / coil_dt, 0.0), 1.0)

        # Equipment: it stops without supply and restarts when supply returns.
        tripped = trip >= 0.5 or fan_loss >= 0.9
        running = run_cmd and not tripped and _live(state, flag)
        condensing = crac_condensing_c(dry, demand if running else 0.0, derate)
        head = min(max(1.0 - (condensing - HEAD_LIMIT_C) / (2.0 * HEAD_UNLOAD_K), 0.5), 1.0)
        compressor = min(demand, (1.0 - derate) * head) if running else 0.0
        leaving = return_c - compressor * coil_dt
        s["run_cmd"] = run_cmd
        s["running"] = running
        s["tripped"] = tripped
        s["fan_pct"] = fan * (1.0 - fan_loss) if running else 0.0
        airflow = fan / NOMINAL_FAN_PCT * (1.0 - fan_loss) * (1.0 - blockage) if running else 0.0
        s["airflow"] = airflow
        s["compressor_pct"] = 100.0 * compressor
        s["compressor1_pct"] = 100.0 * min(compressor / COMPRESSOR1_SHARE, 1.0)
        s["compressor2_pct"] = 100.0 * max(
            (compressor - COMPRESSOR1_SHARE) / (1.0 - COMPRESSOR1_SHARE), 0.0
        )
        s["condensing_c"] = condensing
        s["return_c"] = return_c
        supply = s["supply_c"] + (leaving - s["supply_c"]) * settle
        s["supply_c"] = supply
        s["cooling_kw"] = max(cap * airflow * (return_c - supply), 0.0)
        _humidity(s, zone, supply)

        # Device alarm logic, reading the unit's own state
        s["alarm_filter"] = running and blockage >= 0.25
        s["alarm_high_pressure"] = trip >= 0.5 or (
            compressor > 0.0 and (derate >= 0.4 or condensing >= HP_ALARM_C)
        )
        s["alarm_trip"] = tripped
        s["alarm_loss_of_signal"] = s.get("comm", "good") == "bad"
        s["has_alarm"] = (
            s["alarm_filter"]
            or s["alarm_high_pressure"]
            or s["alarm_trip"]
            or s["alarm_loss_of_signal"]
        )


class ChilledWaterUnitDomain:
    """The air units on chilled water, the CDUs, and the Cooling Blocks that feed the halls'.
    It steps after the chiller plant, on the water the plant supplied this step, and before
    the site loads and the thermal zones."""

    settling_s = int(6 * VALVE_TAU_S)

    def initial(self, ctx: StepContext) -> dict[str, AssetState]:
        states: dict[str, AssetState] = {}
        for unit, type_id in chw_units(ctx.design).items():
            s: AssetState = {
                "running": True,
                "tripped": False,
                "chws_c": CHWS_SP_C,
                "chwr_c": CHWS_SP_C + CHW_DT_K,
                "flow_lps": 0.0,
                "valve_pct": 100.0,
                "cooling_kw": 0.0,
                "chw_demand_kw": 0.0,
                "chw_take_kw": 0.0,
                "asked_lps": 0.0,
            }
            if type_id == LIQUID_TYPE:
                s |= {"it_kw": 0.0, "removed_kw": 0.0, "pue": 1.0}
                s |= {"it_kwh": 0.0, "facility_kwh": 0.0}
                s |= {f"pump{k}_kwh": 0.0 for k in range(1, CDU_PUMPS + 1)}
            else:
                s |= {
                    "airflow": 1.0,
                    "setpoint_c": UNIT_SUPPLY_SP_C,
                    "return_c": HALL_C,
                    "supply_c": SUPPLY_C,
                    "return_rh_pct": 50.0,
                    "supply_rh_pct": 50.0,
                    "static_kpa": STATIC_KPA,
                    "energy_kwh": 0.0,
                    "alarm_filter": False,
                    "alarm_airflow": False,
                    "alarm_trip": False,
                    "alarm_fault": False,
                    "alarm_loss_of_signal": False,
                    "has_alarm": False,
                }
            if type_id == FRESH_AIR_TYPE:
                s |= {"fire_alarm": False, "dehumidifying": True}
                s |= {"return_sp_c": UNIT_RETURN_SP_C, "return_rh_sp_pct": RETURN_RH_SP_PCT}
                s["supply_rh_sp_pct"] = SUPPLY_RH_SP_PCT
            states[unit] = s
        for block in cooling_blocks(ctx.design):
            states[block] = {
                "flow_lps": 0.0,
                "chws_c": CHWS_SP_C,
                "chwr_c": CHWS_SP_C + CHW_DT_K,
                "hall_supply_c": CHWS_SP_C,
                "mv1_pct": 100.0,
                "mv2_pct": 100.0,
            }
        return states

    def complete(self, state: WorldState, ctx: StepContext) -> None:
        self._update(state, ctx, settle=True)

    def handles(self, event: Event, design: PlantDesign) -> bool:
        return False

    def apply(self, event: Event, state: WorldState) -> None:
        raise AssertionError("no events")

    def step(self, state: WorldState, ctx: StepContext) -> None:
        self._update(state, ctx, settle=False)

    def _update(self, state: WorldState, ctx: StepContext, settle: bool) -> None:
        a, dt = state.assets, ctx.dt
        plant = a.get(PLANT)
        if plant is not None:
            chws, delivery, plant_lps = plant["chws_c"], plant["delivery"], plant["flow_lps"]
        else:  # a world without the plant: ideal chilled water
            chws, delivery, plant_lps = SUPPLY_C - COIL_APPROACH_K, 1.0, math.inf
        coil = chws + COIL_APPROACH_K
        valve_blend = 1.0 if settle else min(dt / VALVE_TAU_S, 1.0)
        stagnant = 1.0 if settle else min(dt / STAGNANT_TAU_S, 1.0)
        hours = 0.0 if settle else dt / 3600.0
        air = site_air(state, ctx.design)
        fresh_kw = FRESH_AIR_KG_S * max(
            enthalpy_kj_per_kg(air["dry_bulb_c"], air["dew_point_c"], air["pressure_hpa"])
            - enthalpy_kj_per_kg(OFF_COIL_C, OFF_COIL_C, air["pressure_hpa"]),
            0.0,
        )
        can_dry = delivery >= 0.5 and chws <= DEHUMIDIFY_MAX_CHWS_C
        liquid_factor = min(max((FWS_MAX_C - chws) / (FWS_MAX_C - CHWS_SP_C), 0.0), 1.0)

        plan = _unit_plan(ctx.design)
        asked_total = 0.0
        for u in plan:
            s = a[u.id]
            zone = a[u.room]
            temp = zone["temp_c"]
            flag = u.flag
            live = flag is None or a.get(flag, _LIVE).get("live", True)
            fan_kw = s.get("power_kw", 0.0)
            if u.liquid:
                it = 0.0
                for node in u.it:
                    it += a[node]["power_kw"]
                it *= u.liquid_share
                running = live and s.get("constraint.trip", 0.0) < 0.5
                s["running"] = running
                s["it_kw"] = it
                demand = it if running else 0.0
                take = demand * liquid_factor
                s["removed_kw"] = take * delivery
                s["cooling_kw"] = s["removed_kw"]
                s["chw_demand_kw"], s["chw_take_kw"] = demand, take
                s["asked_lps"] = demand / (CP * CHW_DT_K)
                s["valve_pct"] = 100.0 if running else 0.0
                asked_total += s["asked_lps"]
                if hours:
                    pump = fan_kw / CDU_DUTY_PUMPS
                    for k in range(1, CDU_DUTY_PUMPS + 1):
                        s[f"pump{k}_kwh"] += pump * hours
                    s["it_kwh"] += it * hours
                    s["facility_kwh"] += (it + fan_kw) * hours
                s["pue"] = (it + fan_kw) / it if it > 0.0 else 1.0
                continue

            lost = s.get("comm", "good") == "bad"
            if _UNIT_INPUTS.isdisjoint(s):  # healthy, as nearly every unit is
                fire = tripped = False
                fan_loss = blockage = 0.0
            else:
                fire = u.fresh_air and s.get("constraint.fire_alarm", 0.0) >= 0.5
                tripped = s.get("constraint.trip", 0.0) >= 0.5
                fan_loss = s.get("constraint.fan_loss", 0.0)
                blockage = s.get("constraint.filter_blockage", 0.0)
            running = live and not fire and not tripped
            airflow = (1.0 - fan_loss) * (1.0 - blockage) if running else 0.0

            # Valve Controller on supply air: the share of full flow that brings the coil's
            # leaving air to setpoint.
            sp = s["setpoint_c"]
            if not running or temp <= sp:
                need = 0.0
            elif temp <= coil:
                need = 1.0
            else:
                need = min((temp - sp) / (temp - coil), 1.0)
            valve_pct = s["valve_pct"]
            valve_pct += (100.0 * need - valve_pct) * valve_blend
            valve_pct = 0.0 if valve_pct < 0.0 else 100.0 if valve_pct > 100.0 else valve_pct
            valve = valve_pct / 100.0
            supply = temp - (temp - coil) * valve * delivery if running and temp > coil else temp
            cap = u.cap * airflow
            if running:
                extra = fan_kw + fresh_kw if u.fresh_air else fan_kw
                demand = cap * (temp - sp) + extra if temp > sp else extra
                take = cap * (temp - coil) * valve + extra if temp > coil else extra
            else:
                demand = take = 0.0
            asked = demand * valve / (CP * CHW_DT_K)
            asked_total += asked
            s["valve_pct"] = valve_pct
            s["chw_demand_kw"], s["chw_take_kw"], s["asked_lps"] = demand, take, asked
            s["running"], s["tripped"], s["airflow"] = running, tripped, airflow
            s["return_c"], s["supply_c"] = temp, supply
            s["cooling_kw"] = cap * (temp - supply)
            s["static_kpa"] = STATIC_KPA * airflow * airflow * (1.0 + blockage)
            if hours:
                s["energy_kwh"] += fan_kw * hours
            _humidity(s, zone, supply)
            if u.fresh_air:
                s["fire_alarm"] = fire
                s["dehumidifying"] = running and can_dry
            alarm_filter = running and blockage >= 0.25
            alarm_airflow = running and airflow < 0.5
            s["alarm_filter"], s["alarm_airflow"] = alarm_filter, alarm_airflow
            s["alarm_trip"], s["alarm_loss_of_signal"] = tripped, lost
            s["alarm_fault"] = tripped or alarm_airflow
            s["has_alarm"] = alarm_filter or alarm_airflow or tripped or lost or fire

        # The water: the plant's flow shared out in proportion to what each unit asks.
        if plant_lps == math.inf:
            scale = 1.0
        else:
            scale = plant_lps / asked_total if asked_total > 0.0 else 0.0
        for u in plan:
            s = a[u.id]
            flow = s["asked_lps"] * scale
            s["flow_lps"] = flow
            if flow > 0.0:
                heat = s["chw_take_kw"] * delivery
                s["chws_c"] = chws
                s["chwr_c"] = chws + heat / (CP * flow)
            else:  # the water standing in the coil warms towards the room
                temp = a[u.room]["temp_c"]
                s["chws_c"] += (temp - s["chws_c"]) * stagnant
                s["chwr_c"] += (temp - s["chwr_c"]) * stagnant
        for block, units in _block_plan(ctx.design):
            b = a[block]
            flow = weighted = valves = 0.0
            for unit in units:
                s = a[unit]
                flow += s["flow_lps"]
                weighted += s["flow_lps"] * s["chwr_c"]
                valves += s["flow_lps"] * s["valve_pct"]
            b["flow_lps"] = flow
            if flow > 0.0:
                b["chws_c"] = chws
                b["chwr_c"] = weighted / flow
                b["hall_supply_c"] = chws + RISER_GAIN_KW / (CP * flow)
                b["mv1_pct"] = min(valves / flow, 100.0)
            else:
                hall = a[units[0]]["chwr_c"] if units else b["chwr_c"]
                b["chws_c"] += (hall - b["chws_c"]) * stagnant
                b["chwr_c"] = hall
                b["hall_supply_c"] = b["chws_c"]
            b["mv2_pct"] = 100.0


def _humidity(s: AssetState, zone: AssetState | None, supply_c: float) -> None:
    """A unit's return air carries its zone's moisture; its supply air the same moisture, less
    what condenses on a coil colder than the zone's dew point (`relative_humidity_pct`)."""
    if zone is None:
        return
    dew = min(zone["dew_point_c"], supply_c)
    s["return_rh_pct"] = zone["rh_pct"]
    s["supply_rh_pct"] = 100.0 * math.exp(
        _MAGNUS_A * dew / (dew + _MAGNUS_B) - _MAGNUS_A * supply_c / (supply_c + _MAGNUS_B)
    )


_LIVE: AssetState = {}
_MAGNUS_A, _MAGNUS_B = 17.625, 243.04
"""The Magnus coefficients of `weather.saturation_kpa`, for the ratio of two saturation
pressures in one exponential."""
_CRAC_INPUTS = frozenset(
    {
        "constraint.fan_loss",
        "constraint.filter_blockage",
        "constraint.compressor_trip",
        "constraint.condenser_derate",
        "controller.setpoint_offset_c",
    }
)
"""The Physical Constraints and Controller faults that take a CRAC off its healthy path."""
_UNIT_INPUTS = frozenset(
    {
        "constraint.fire_alarm",
        "constraint.trip",
        "constraint.fan_loss",
        "constraint.filter_blockage",
    }
)
"""The Physical Constraints that take a chilled-water air unit off its healthy path."""


def _live(state: WorldState, flag: str | None) -> bool:
    """`electrical.powered`, given the node's supply flag."""
    return flag is None or state.assets.get(flag, _LIVE).get("live", True)


# ---- Plant Design lookups (derived once per design; the design is immutable)


@functools.cache
def cracs(design: PlantDesign) -> tuple[str, ...]:
    return tuple(a.path for a in design.assets.values() if a.type_id == CRAC_TYPE and a.room)


@functools.cache
def chw_units(design: PlantDesign) -> dict[str, str]:
    """Every unit on chilled water that serves a zone → its type."""
    found = {}
    for a in design.assets.values():
        if a.type_id in CHW_UNIT_TYPES and a.room and served_room(design, a.path):
            found[a.path] = a.type_id
    for u in design.unexported.values():
        if u.type_id in CHW_UNIT_TYPES and served_room(design, u.id):
            found[u.id] = u.type_id
    return found


@functools.cache
def cooling_blocks(design: PlantDesign) -> dict[str, tuple[str, ...]]:
    """Every Cooling Block → the units it feeds."""
    units = chw_units(design)
    return {
        u.id: tuple(n for n in design.downstream(u.id, ConnectionKind.CHW) if n in units)
        for u in design.unexported.values()
        if u.type_id == COOLING_BLOCK_TYPE
    }


def _share(design: PlantDesign, room: str, unit: str) -> float:
    return dict(air_suppliers(design, room)).get(unit, 0.0)


@functools.cache
def _crac_plan(design: PlantDesign) -> tuple[tuple[str, str | None, float, str | None], ...]:
    """(CRAC, the room it serves, its share of the room's UA, its supply flag)."""
    flags = network(design).supply_flag
    plan = []
    for crac in cracs(design):
        room = served_room(design, crac)
        cap = _share(design, room, crac) * ua_kw_per_k(design, room) if room else 0.0
        plan.append((crac, room, cap, flags.get(crac)))
    return tuple(plan)


@dataclass(frozen=True, slots=True)
class _Unit:
    id: str
    room: str
    cap: float
    """Heat its coil removes per kelvin of return air above supply, at full airflow."""
    flag: str | None
    fresh_air: bool
    liquid: bool
    it: tuple[str, ...]
    """For a CDU, the IT equipment of its pod's hall."""
    liquid_share: float
    """For a CDU, its share of the hall's IT Load."""


@functools.cache
def _unit_plan(design: PlantDesign) -> tuple[_Unit, ...]:
    flags = network(design).supply_flag
    units = chw_units(design)
    plan = []
    for unit, type_id in units.items():
        room = served_room(design, unit)
        liquid = type_id == LIQUID_TYPE
        share = 0.0
        if liquid:
            basis = design.it_basis.get(room)
            pods = sum(
                1 for n, t in units.items() if t == LIQUID_TYPE and served_room(design, n) == room
            )
            share = basis.liquid_fraction / pods if basis is not None else 0.0
        plan.append(
            _Unit(
                unit,
                room,
                _share(design, room, unit) * ua_kw_per_k(design, room),
                flags.get(unit),
                type_id == FRESH_AIR_TYPE,
                liquid,
                it_in(design, room) if liquid else (),
                share,
            )
        )
    return tuple(plan)


@functools.cache
def _block_plan(design: PlantDesign) -> tuple[tuple[str, tuple[str, ...]], ...]:
    return tuple(cooling_blocks(design).items())


__all__ = [
    "CCU_TYPE",
    "CHW_UNIT_TYPES",
    "COOLING_BLOCK_TYPE",
    "CRAC_TYPE",
    "FRESH_AIR_TYPE",
    "LIQUID_TYPE",
    "ChilledWaterUnitDomain",
    "CracDomain",
    "chw_units",
    "cooling_blocks",
    "crac_condensing_c",
    "cracs",
]
