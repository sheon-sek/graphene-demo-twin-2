"""Site power: what every electrical consumer draws each step, and the site totals the
Dashboard reads.

Every consumer node carries `power_kw`, the real power it draws (or, for UPS modules and
transformers, dissipates) this step, never negative, and belongs to one LoadClass on one floor.
Facility power is the sum over all of them and IT power the sum over the IT equipment, so PUE
and every energy total come from the same step. The electrical network's sources supply the
facility power plus what the UPS batteries store (`storage_kw`), which is negative while they
carry the load.

Two domains share the work around the electrical network (`sim.electrical`). SiteLoadDomain
steps before it: until the chiller plant and airside (P2), water (P3) and building services
(P4) are modelled, it is their stand-in, working out each non-IT load from the state the other
domains have reached (CRAC units, the zones' air, outdoor air) with simple plant curves,
and nothing draws while its supply is dead. The electrical network adds the UPS and transformer
losses, and SitePowerDomain steps last to total everything up.
"""

import functools
import math
from collections.abc import Mapping
from enum import StrEnum

from graphene_demo_twin.plant_design import ConnectionKind, PlantDesign
from graphene_demo_twin.sim.electrical import (
    control_ups,
    hall_ups,
    incomers,
    network,
    ups_rating,
)
from graphene_demo_twin.sim.engine import StepContext
from graphene_demo_twin.sim.events import Event
from graphene_demo_twin.sim.it_load import IT_TYPE
from graphene_demo_twin.sim.placeholder import assets_of
from graphene_demo_twin.sim.state import AssetState, WorldState
from graphene_demo_twin.sim.thermal import (
    CRAC_TYPE,
    air_suppliers,
    served_room,
    supplier_air,
    ua_kw_per_k,
    zones,
)
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
    """Everything else: the control UPS's BMS and network load, water pumps, lifts, fuel
    system, genset auxiliaries and the control-room circuits."""


_CLASS_OF_TYPE = {
    IT_TYPE: LoadClass.IT,
    CRAC_TYPE: LoadClass.COOLING,
    "Chiller": LoadClass.COOLING,
    "Chiller Pump": LoadClass.COOLING,
    "CDU": LoadClass.COOLING,
    "Cooling Tower": LoadClass.HEAT_REJECTION,
    "Makeup Water Pump": LoadClass.HEAT_REJECTION,
    "CW Transfer Pump": LoadClass.OTHER,
    "CW Booster Pump": LoadClass.OTHER,
    "AC Makeup Pump": LoadClass.OTHER,
    "IPS": LoadClass.OTHER,
    "RCMS": LoadClass.OTHER,
    "Diesel": LoadClass.OTHER,
    "PAHU": LoadClass.VENTILATION,
    "FCU": LoadClass.VENTILATION,
    "FWU": LoadClass.VENTILATION,
    "Ceiling Cooling Units": LoadClass.VENTILATION,
}
FAN_KW = {"PAHU": 11.0, "FCU": 2.2, "FWU": 3.0, "Ceiling Cooling Units": 7.5}
"""Nominal fan power of the air units the airside (P2) does not model yet."""
CRAC_FAN_KW = 15.0
"""CRAC EC fan power at full speed; it follows the cube of speed."""
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
SERVICE_KW = {
    "CDU": 7.5,
    "CW Transfer Pump": 1.8,
    "CW Booster Pump": 2.2,
    "AC Makeup Pump": 0.2,
    "IPS": 2.5,
    "RCMS": 4.0,
}
"""Average draw of equipment whose physics comes later (P3 water, the CDU loop, the control
room's critical circuits)."""
MAKEUP_PUMP_KW, MAKEUP_PUMP_LPH = 0.9, 1000.0
"""A tower cell's makeup pump at its rated flow; it idles at a fifth of that."""
LIFT_KW, LIFT_BUSY_KW = 6.0, 6.0
"""Lifts 1–3 beyond Meter14: standing losses, and the extra during office hours."""
GENSET_AUX_KW = 30.0
"""Jacket-water heaters and battery chargers of the six gensets, beyond Meter16."""
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


class SiteLoadDomain:
    """The draw of every consumer the electrical network does not model itself, other than the
    IT equipment: cooling, heat rejection, ventilation, lighting and building services. It must
    step after the CRAC domain and before the electrical network; it reads the zones' air as
    the previous step left it, as the zones do when they work out their cooling."""

    settling_s = 0

    def initial(self, ctx: StepContext) -> dict[str, AssetState]:
        states: dict[str, AssetState] = {node: {"power_kw": 0.0} for node in site_loads(ctx.design)}
        states[SITE] = dict.fromkeys(PLANT_VARIABLES, 0.0)
        return states

    def complete(self, state: WorldState, ctx: StepContext) -> None:
        self.step(state, ctx)

    def handles(self, event: Event, design: PlantDesign) -> bool:
        return False

    def apply(self, event: Event, state: WorldState) -> None:
        raise AssertionError("no events")

    def step(self, state: WorldState, ctx: StepContext) -> None:
        design = ctx.design
        assets = state.assets
        net = network(design)
        flag = net.supply_flag
        if net.order and net.order[0] in assets:

            def fed(node: str) -> bool:
                return assets[flag[node]]["live"]

        else:  # a world without the electrical network: everything has supply

            def fed(node: str) -> bool:
                return True

        power: dict[str, float] = {}
        air = site_air(state, design)
        dry, dew, wb, hpa = (
            air["dry_bulb_c"],
            air["dew_point_c"],
            air["wet_bulb_c"],
            air["pressure_hpa"],
        )

        # DX CRAC units: fans, and compressors rejecting to the outdoor air. A unit without
        # supply has already stopped.
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
            power[unit] = FAN_KW[type_id] if fed(unit) else 0.0
            chw_load += power[unit]
        fresh = FRESH_AIR_KG_S * (
            enthalpy_kj_per_kg(dry, dew, hpa) - enthalpy_kj_per_kg(OFF_COIL_C, OFF_COIL_C, hpa)
        )
        chw_load += max(fresh, 0.0) * sum(fed(u) for u in assets_of(design, "PAHU"))
        for zone in zones(design):
            temp = state.assets[zone]["temp_c"]
            for supplier, share in air_suppliers(design, zone):
                if supplier not in assets_of(design, CRAC_TYPE) and fed(supplier):
                    airflow, supply_c = supplier_air(state, design, supplier)
                    chw_load += share * airflow * (temp - supply_c) * ua_kw_per_k(design, zone)
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
        pumps = assets_of(design, "Makeup Water Pump")
        each = min(makeup / max(len(pumps), 1) / MAKEUP_PUMP_LPH, 1.5)
        for pump in pumps:
            power[pump] = MAKEUP_PUMP_KW * (0.2 + 0.8 * each)

        # Building services, until their own physics arrives.
        for node, type_id in services(design).items():
            power[node] = SERVICE_KW[type_id]

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

        # The draw beyond the meters with nothing authored below them.
        for meter in stand_in_meters(design):
            if meter.endswith("Meter14"):
                power[meter] = LIFT_KW + (LIFT_BUSY_KW if office else 0.0)
            else:
                power[meter] = GENSET_AUX_KW

        for node, kw in power.items():
            assets[node]["power_kw"] = kw if fed(node) else 0.0
        site = state.assets[SITE]
        site["chw_load_kw"] = chw_load
        site["chw_plant_kw"] = sum(
            state.assets[n]["power_kw"] for leg in plant.legs for n in (*leg[:3], *leg[3])
        ) + sum(state.assets[p]["power_kw"] for p in plant.distribution)
        site["cooling_load_kw"] = dx_cooling + chw_load
        site["makeup_lph"] = makeup


class SitePowerDomain:
    """The site totals, each floor's energy per load class and the running averages, over every
    consumer's power this step. It must step after every other domain."""

    settling_s = 0

    def initial(self, ctx: StepContext) -> dict[str, AssetState]:
        return {SITE: {name: 0.0 for name in site_variables(ctx.design)}}

    def complete(self, state: WorldState, ctx: StepContext) -> None:
        self._totals(state, ctx.design)
        site = state.assets[SITE]
        for window in AVERAGE_WINDOWS:
            for q in AVERAGED:
                site[f"avg.{window}.{q}"] = site[q]

    def handles(self, event: Event, design: PlantDesign) -> bool:
        return False

    def apply(self, event: Event, state: WorldState) -> None:
        raise AssertionError("no events")

    def step(self, state: WorldState, ctx: StepContext) -> None:
        self._totals(state, ctx.design)
        site = state.assets[SITE]
        hours = ctx.dt / 3600.0
        for node, key in _energy_keys(ctx.design):
            site[key] += state.assets[node]["power_kw"] * hours
        for window, tau in AVERAGE_WINDOWS.items():
            for q in AVERAGED:
                key = f"avg.{window}.{q}"
                site[key] += (site[q] - site[key]) * ctx.dt / tau

    def _totals(self, state: WorldState, design: PlantDesign) -> None:
        totals = dict.fromkeys(LoadClass, 0.0)
        for node, cls in _classes(design):
            totals[cls] += state.assets[node]["power_kw"]
        site = state.assets[SITE]
        for cls, kw in totals.items():
            site[f"{cls}_kw"] = kw
        site["facility_kw"] = sum(totals.values())
        site["cooling_elec_kw"] = (
            totals[LoadClass.COOLING]
            + totals[LoadClass.HEAT_REJECTION]
            + totals[LoadClass.VENTILATION]
        )
        site["transformer_loss_kw"] = sum(state.assets[i]["power_kw"] for i in incomers(design))
        site["utility_kw"] = sum(state.assets[i]["p_kw"] for i in incomers(design))
        net = network(design)
        site["storage_kw"] = sum(state.assets[u]["battery_kw"] for u in net.ups)
        site["ups_capacity_kw"] = sum(
            ups_rating(net, u)[0] for m in hall_ups(design).values() for u in m
        )


def _crac_cooling_kw(state: WorldState, design: PlantDesign, crac: str) -> float:
    """Heat a CRAC unit takes out of the room it serves."""
    s = state.assets[crac]
    room = served_room(design, crac)
    if room is None:
        return 0.0
    share = dict(air_suppliers(design, room))[crac]
    zone = state.assets[room]
    removed = share * s["airflow"] * (zone["temp_c"] - s["supply_c"]) * ua_kw_per_k(design, room)
    return max(removed, 0.0)


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
def services(design: PlantDesign) -> dict[str, str]:
    """Placed building-service loads with a fixed stand-in draw → their type."""
    return {a.path: a.type_id for a in design.assets.values() if a.type_id in SERVICE_KW and a.room}


@functools.cache
def stand_in_meters(design: PlantDesign) -> tuple[str, ...]:
    """Meters with nothing authored below them, whose draw beyond is a stand-in."""
    return tuple(f.meter for f in network(design).feeds if f.stand_in)


@functools.cache
def site_loads(design: PlantDesign) -> tuple[str, ...]:
    """The consumers whose draw SiteLoadDomain works out: all but the IT equipment and what
    the electrical network owns (UPS, transformers, the diesel tanks' fuel pumps)."""
    net = network(design)
    owned = {*net.ups, *net.incomers, *net.tanks}
    return tuple(
        n for n, (cls, _) in consumers(design).items() if cls is not LoadClass.IT and n not in owned
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
    others = {*control_ups(design), *stand_in_meters(design)}
    for a in design.assets.values():
        if a.room is None:
            continue
        if a.path in losses:
            cls = LoadClass.LOSSES
        elif a.path in others:
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


PLANT_VARIABLES = ("chw_load_kw", "chw_plant_kw", "cooling_load_kw", "makeup_lph")
"""Site variables SiteLoadDomain owns: the stand-in plant's loads."""


@functools.cache
def _classes(design: PlantDesign) -> tuple[tuple[str, LoadClass], ...]:
    return tuple((node, cls) for node, (cls, _) in consumers(design).items())


@functools.cache
def _energy_keys(design: PlantDesign) -> tuple[tuple[str, str], ...]:
    """(consumer, site variable its energy adds to), for every non-IT consumer."""
    return tuple(
        (node, energy_key(floor, cls))
        for node, (cls, floor) in consumers(design).items()
        if cls is not LoadClass.IT
    )


@functools.cache
def site_variables(design: PlantDesign) -> tuple[str, ...]:
    """Site variables SitePowerDomain owns."""
    names = [f"{cls}_kw" for cls in LoadClass]
    names += ["facility_kw", "cooling_elec_kw", "transformer_loss_kw", "utility_kw"]
    names += ["storage_kw", "ups_capacity_kw"]
    names += sorted(
        {
            energy_key(floor, cls)
            for cls, floor in consumers(design).values()
            if cls is not LoadClass.IT
        }
    )
    names += [f"avg.{w}.{q}" for w in AVERAGE_WINDOWS for q in AVERAGED]
    return tuple(names)
