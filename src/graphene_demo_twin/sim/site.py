"""Site power: what every electrical consumer draws each step, and the site totals the
Dashboard reads.

Every consumer node carries `power_kw`, the real power it draws (or, for UPS modules and
transformers, dissipates) this step, and belongs to one LoadClass on one floor. Facility
power is the sum over all of them and IT power the sum over the IT equipment, so PUE and
every energy total come from the same step's power flow.

Until the electrical network (#19) and the chiller plant and airside (P2) are modelled, this
domain is their stand-in: it works out the non-IT loads from the state the other domains
have reached this step (IT Load, CRAC units, hall cooling, outdoor air) with simple plant
curves. It must step last.
"""

import functools
import math
from collections.abc import Mapping
from enum import StrEnum

from graphene_demo_twin.plant_design import ConnectionKind, PlantDesign
from graphene_demo_twin.sim.engine import StepContext
from graphene_demo_twin.sim.events import Event
from graphene_demo_twin.sim.it_load import IT_TYPE, it_equipment, it_heat_kw
from graphene_demo_twin.sim.placeholder import (
    CRAC_TYPE,
    air_suppliers,
    assets_of,
    halls,
    served_room,
    supplier_air,
    ua_kw_per_k,
)
from graphene_demo_twin.sim.state import AssetState, WorldState
from graphene_demo_twin.sim.weather import DAY_S, LOCAL_OFFSET_S, enthalpy_kj_per_kg, site_air

SITE = "site"
"""World-state key of the site as a whole: its power totals, the energy each floor has used
per load class, and the running averages the Dashboard reports. Not a Plant Design node."""

KW_PER_RT = 3.51685
"""One refrigeration ton."""
AVERAGE_WINDOWS: Mapping[str, int] = {"day": DAY_S, "month": 30 * DAY_S, "year": 365 * DAY_S}
"""Time constants of the running averages behind the daily, monthly and annual KPIs."""
AVERAGED = ("facility_kw", "it_kw", "makeup_lph", "cooling_elec_kw", "cooling_load_kw")


class LoadClass(StrEnum):
    """What a consumer's power is spent on; the Dashboard's energy breakdown."""

    IT = "it"
    COOLING = "cooling"
    """Chillers, their pumps and DX CRAC units."""
    HEAT_REJECTION = "heat_rejection"
    """Cooling tower fans."""
    VENTILATION = "ventilation"
    """Air handler, fan-coil and ceiling-unit fans."""
    LIGHTING = "lighting"
    LOSSES = "losses"
    """UPS and transformer losses."""
    OTHER = "other"
    """Everything else: the control UPS's BMS and network load."""


_CLASS_OF_TYPE = {
    IT_TYPE: LoadClass.IT,
    CRAC_TYPE: LoadClass.COOLING,
    "Chiller": LoadClass.COOLING,
    "Chiller Pump": LoadClass.COOLING,
    "Cooling Tower": LoadClass.HEAT_REJECTION,
    "PAHU": LoadClass.VENTILATION,
    "FCU": LoadClass.VENTILATION,
    "FWU": LoadClass.VENTILATION,
    "Ceiling Cooling Units": LoadClass.VENTILATION,
}
FAN_KW = {"PAHU": 11.0, "FCU": 2.2, "FWU": 3.0, "Ceiling Cooling Units": 7.5}
"""Nominal fan power of the air units the airside (P2) does not model yet."""
CRAC_FAN_KW = 15.0
"""CRAC EC fan power at full speed; it follows the cube of speed."""
CRAC_SUPPORT_KW = 60.0
"""Cooling a CRAC outside the Data Halls delivers at full compressor load."""
FRESH_AIR_KG_S = 2.0
"""Outdoor air each primary air handler brings in and dehumidifies on its chilled-water coil."""
OFF_COIL_C = 12.5
"""Air leaving a fresh-air coil, nearly saturated."""
CHILLER_KWR = 3500.0
DUTY_CHILLERS = 3
TOWER_CELL_KW = 1000.0
"""Heat a tower cell rejects at 27 °C wet bulb, full fan."""
TOWER_FAN_KW = 11.0
CHW_PUMP_KW, CW_PUMP_KW, DISTRIBUTION_PUMP_KW = 18.5, 30.0, 37.0
CYCLES_OF_CONCENTRATION = 4.0
LATENT_KJ_PER_KG = 2430.0
UPS_KVA, UPS_PF = 500.0, 0.9
UPS_NO_LOAD_KW, UPS_LOSS = 2.0, 0.035
CONTROL_LOAD_KW = 45.0
"""BMS control room and network load on the control UPS."""
TRANSFORMER_NO_LOAD_KW, TRANSFORMER_LOSS = 4.5, 0.008
LIGHTING_W_M2 = {
    "hall": 2.0,
    "support": 9.0,
    "electrical": 5.0,
    "water": 4.0,
    "cooling": 4.0,
    "airside": 3.0,
    "core": 8.0,
    "corridor": 7.0,
    "fuel": 0.0,
}
OCCUPIED_KINDS = frozenset({"support", "core", "corridor"})
"""Rooms whose lights follow office hours; the rest are lit around the clock."""
OUTDOOR_W_M2 = 1.0
"""Floodlighting on outdoor areas, from dusk to dawn."""


class SitePowerDomain:
    """Every consumer's power this step, the site totals, each floor's energy per load class
    and the running averages. The IT equipment's power and energy are the IT Load domain's;
    this domain adds everything else and must step after every other domain."""

    settling_s = 0

    def initial(self, ctx: StepContext) -> dict[str, AssetState]:
        states: dict[str, AssetState] = {
            node: {"power_kw": 0.0}
            for node, (cls, _) in consumers(ctx.design).items()
            if cls is not LoadClass.IT
        }
        site: AssetState = {name: 0.0 for name in site_variables(ctx.design)}
        states[SITE] = site
        return states

    def complete(self, state: WorldState, ctx: StepContext) -> None:
        self._flow(state, ctx)
        site = state.assets[SITE]
        for window in AVERAGE_WINDOWS:
            for q in AVERAGED:
                site[f"avg.{window}.{q}"] = site[q]

    def handles(self, event: Event, design: PlantDesign) -> bool:
        return False

    def apply(self, event: Event, state: WorldState) -> None:
        raise AssertionError("no events")

    def step(self, state: WorldState, ctx: StepContext) -> None:
        self._flow(state, ctx)
        site = state.assets[SITE]
        for node, (cls, floor) in consumers(ctx.design).items():
            if cls is not LoadClass.IT:
                site[energy_key(floor, cls)] += state.assets[node]["power_kw"] * ctx.dt / 3600.0
        for window, tau in AVERAGE_WINDOWS.items():
            for q in AVERAGED:
                key = f"avg.{window}.{q}"
                site[key] += (site[q] - site[key]) * ctx.dt / tau

    def _flow(self, state: WorldState, ctx: StepContext) -> None:
        design = ctx.design
        power: dict[str, float] = {}
        air = site_air(state, design)
        dry, dew, wb, hpa = (
            air["dry_bulb_c"],
            air["dew_point_c"],
            air["wet_bulb_c"],
            air["pressure_hpa"],
        )

        # DX CRAC units: fans, and compressors rejecting to the outdoor air.
        dx_cooling = 0.0
        for crac in assets_of(design, CRAC_TYPE):
            s = state.assets[crac]
            cooling = _crac_cooling_kw(state, design, crac)
            cop = (3.4 - 0.08 * (dry - 27.0)) * (
                1.0 - 0.5 * s.get("constraint.condenser_derate", 0.0)
            )
            compressor = cooling / cop if s["compressor_pct"] > 0.0 else 0.0
            power[crac] = CRAC_FAN_KW * (s["fan_pct"] / 100.0) ** 3 + compressor
            dx_cooling += cooling

        # Air units on chilled water: their fans, and the heat their coils take up.
        chw_load = 0.0
        for unit, type_id in air_units(design).items():
            power[unit] = FAN_KW[type_id]
            chw_load += FAN_KW[type_id]
        fresh = FRESH_AIR_KG_S * (
            enthalpy_kj_per_kg(dry, dew, hpa) - enthalpy_kj_per_kg(OFF_COIL_C, OFF_COIL_C, hpa)
        )
        chw_load += max(fresh, 0.0) * len(assets_of(design, "PAHU"))
        for hall in halls(design):
            temp = state.assets[hall]["temp_c"]
            for supplier, share in air_suppliers(design, hall):
                if supplier not in assets_of(design, CRAC_TYPE):
                    airflow, supply_c = supplier_air(state, supplier)
                    chw_load += share * airflow * (temp - supply_c) * ua_kw_per_k(design, hall)
        chw_load = max(chw_load, 0.0)

        # Chiller plant: duty chillers staged on load, lift set by the towers' wet bulb.
        plant = chiller_plant(design)
        running = min(DUTY_CHILLERS, max(1, math.ceil(chw_load / (0.9 * CHILLER_KWR))))
        part_load = chw_load / (running * CHILLER_KWR)
        evaporating, condensing = 12.0 + 273.15, wb + 10.5 + 273.15
        cop = 0.6 * evaporating / (condensing - evaporating) * (1.0 - 0.3 * (1.0 - part_load) ** 2)
        rejected = chw_load
        for i, (chiller, chw_pump, cw_pump, _) in enumerate(plant.legs):
            on = i < running
            power[chiller] = chw_load / running / cop if on else 0.0
            power[chw_pump] = CHW_PUMP_KW if on else 0.0
            power[cw_pump] = CW_PUMP_KW if on else 0.0
            rejected += power[chiller]
        flow = max(chw_load / (DUTY_CHILLERS * CHILLER_KWR), 0.25)
        for pump in plant.distribution:
            power[pump] = DISTRIBUTION_PUMP_KW * flow**2
        capacity = TOWER_CELL_KW * (1.0 + 0.12 * (27.0 - wb))
        for i, (_, _, _, cells) in enumerate(plant.legs):
            for cell in cells:
                load = rejected / (running * len(cells)) if i < running else 0.0
                speed = min(max(load / capacity, 0.15), 1.0) if load > 0.0 else 0.0
                power[cell] = TOWER_FAN_KW * speed**3
        makeup = (
            rejected
            * 3600.0
            / LATENT_KJ_PER_KG
            * (CYCLES_OF_CONCENTRATION / (CYCLES_OF_CONCENTRATION - 1.0))
        )

        # UPS modules lose a share of the IT Load they carry; the control UPS feeds the BMS.
        for hall, modules in hall_ups(design).items():
            load = it_heat_kw(state, design, hall) / len(modules)
            for ups in modules:
                power[ups] = UPS_NO_LOAD_KW + UPS_LOSS * load
        for ups in control_ups(design):
            power[ups] = CONTROL_LOAD_KW + UPS_NO_LOAD_KW

        # Lighting, room by room.
        local = ctx.time + LOCAL_OFFSET_S
        hour = (local % DAY_S) / 3600.0
        office = (local // DAY_S + 3) % 7 < 5 and 8.0 <= hour < 18.0
        dark = hour < 7.0 or hour >= 19.0
        for room in design.rooms.values():
            if room.outdoor:
                watts = OUTDOOR_W_M2 if dark else 0.0
            else:
                watts = LIGHTING_W_M2.get(room.kind, 5.0)
                if room.kind in OCCUPIED_KINDS and not office:
                    watts *= 0.35
            power[room.id] = room.w * room.h * watts / 1000.0

        # Transformers lose a share of everything they carry.
        it = {n: state.assets[n]["power_kw"] for n in it_equipment(design)}
        through = sum(power.values()) + sum(it.values())
        transformers = incomers(design)
        loss = len(transformers) * TRANSFORMER_NO_LOAD_KW + TRANSFORMER_LOSS * through
        for node in transformers:
            power[node] = loss / len(transformers)

        totals = dict.fromkeys(LoadClass, 0.0)
        totals[LoadClass.IT] = sum(it.values())
        for node, kw in power.items():
            state.assets[node]["power_kw"] = kw
            totals[consumers(design)[node][0]] += kw
        site = state.assets[SITE]
        for cls, kw in totals.items():
            site[f"{cls}_kw"] = kw
        site["facility_kw"] = sum(totals.values())
        site["cooling_elec_kw"] = (
            totals[LoadClass.COOLING]
            + totals[LoadClass.HEAT_REJECTION]
            + totals[LoadClass.VENTILATION]
        )
        site["chw_load_kw"] = chw_load
        site["chw_plant_kw"] = sum(
            power[n] for leg in plant.legs for n in (*leg[:3], *leg[3])
        ) + sum(power[p] for p in plant.distribution)
        site["cooling_load_kw"] = dx_cooling + chw_load
        site["makeup_lph"] = makeup
        site["transformer_loss_kw"] = loss
        site["ups_capacity_kw"] = UPS_KVA * UPS_PF * sum(len(m) for m in hall_ups(design).values())


def _crac_cooling_kw(state: WorldState, design: PlantDesign, crac: str) -> float:
    """Heat a CRAC unit takes out of the room it serves."""
    s = state.assets[crac]
    room = served_room(design, crac)
    if room in design.it_basis:
        share = dict(air_suppliers(design, room))[crac]
        hall = state.assets[room]
        removed = (
            share * s["airflow"] * (hall["temp_c"] - s["supply_c"]) * ua_kw_per_k(design, room)
        )
        return max(removed, 0.0)
    return CRAC_SUPPORT_KW * s["compressor_pct"] / 100.0


class _Plant:
    def __init__(self, design: PlantDesign) -> None:
        pumps = set(assets_of(design, "Chiller Pump"))
        legs = []
        for chiller in assets_of(design, "Chiller"):
            chw = next(n for n in design.upstream(chiller, ConnectionKind.CHW) if n in pumps)
            cw = next(n for n in design.upstream(chiller, ConnectionKind.CW) if n in pumps)
            cells = tuple(
                n
                for n in design.downstream(chiller, ConnectionKind.CW)
                if n in design.assets and design.asset(n).type_id == "Cooling Tower"
            )
            legs.append((chiller, chw, cw, cells))
        self.legs: tuple[tuple[str, str, str, tuple[str, ...]], ...] = tuple(legs)
        """(chiller, primary CHW pump, condenser-water pump, tower cells), duty first."""
        on_legs = {n for leg in legs for n in leg[1:3]}
        self.distribution = tuple(p for p in assets_of(design, "Chiller Pump") if p not in on_legs)


@functools.cache
def chiller_plant(design: PlantDesign) -> _Plant:
    return _Plant(design)


@functools.cache
def air_units(design: PlantDesign) -> dict[str, str]:
    """Placed air units other than CRACs → their type."""
    return {a.path: a.type_id for a in design.assets.values() if a.type_id in FAN_KW and a.room}


@functools.cache
def hall_ups(design: PlantDesign) -> dict[str, tuple[str, ...]]:
    """Data Hall → the UPS modules feeding its IT equipment, over the authored power path."""
    modules: dict[str, tuple[str, ...]] = {}
    for node in it_equipment(design):
        hall = design.asset(node).room
        found = [
            n
            for n in design.upstream(node, ConnectionKind.POWER, transitive=True)
            if n in design.assets and design.asset(n).type_id == "UPS"
        ]
        modules[hall] = tuple(dict.fromkeys([*modules.get(hall, ()), *found]))
    return modules


@functools.cache
def control_ups(design: PlantDesign) -> tuple[str, ...]:
    feeding_halls = {u for m in hall_ups(design).values() for u in m}
    return tuple(u for u in assets_of(design, "UPS") if u not in feeding_halls)


@functools.cache
def incomers(design: PlantDesign) -> tuple[str, ...]:
    """The utility incomer meters, each metering one transformer: where the site's power
    enters (a metered power source with nothing upstream of it but the grid)."""
    return tuple(
        a.path
        for a in design.assets.values()
        if a.room
        and a.system == "Electrical"
        and a.type_id == "GPQM144"
        and not design.upstream(a.path, ConnectionKind.POWER)
        and design.downstream(a.path, ConnectionKind.POWER)
    )


def load_class(design: PlantDesign, node: str) -> LoadClass | None:
    """What `node`'s power is spent on, or None if it draws no power in the model."""
    entry = consumers(design).get(node)
    return None if entry is None else entry[0]


@functools.cache
def consumers(design: PlantDesign) -> dict[str, tuple[LoadClass, str]]:
    """Every node that draws power → its load class and floor."""
    found: dict[str, tuple[LoadClass, str]] = {}
    losses = {u for m in hall_ups(design).values() for u in m} | set(incomers(design))
    for a in design.assets.values():
        if a.room is None:
            continue
        if a.path in losses:
            cls = LoadClass.LOSSES
        elif a.path in control_ups(design):
            cls = LoadClass.OTHER
        elif a.type_id in _CLASS_OF_TYPE:
            cls = _CLASS_OF_TYPE[a.type_id]
        else:
            continue
        found[a.path] = (cls, design.room(a.room).floor)
    for room in design.rooms.values():
        found[room.id] = (LoadClass.LIGHTING, room.floor)
    return found


def energy_key(floor: str, cls: LoadClass) -> str:
    """Site variable holding the energy `cls` has used on `floor`, in kWh."""
    return f"energy_kwh.{floor}.{cls}"


@functools.cache
def site_variables(design: PlantDesign) -> tuple[str, ...]:
    names = [f"{cls}_kw" for cls in LoadClass]
    names += [
        "facility_kw",
        "cooling_elec_kw",
        "chw_load_kw",
        "chw_plant_kw",
        "cooling_load_kw",
        "makeup_lph",
        "transformer_loss_kw",
        "ups_capacity_kw",
    ]
    names += sorted(
        {
            energy_key(floor, cls)
            for cls, floor in consumers(design).values()
            if cls is not LoadClass.IT
        }
    )
    names += [f"avg.{w}.{q}" for w in AVERAGE_WINDOWS for q in AVERAGED]
    return tuple(names)
