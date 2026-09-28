"""Site power: what every electrical consumer draws each step, and the site totals the
Dashboard reads.

Every consumer node carries `power_kw`, the real power it draws (or, for UPS modules and
transformers, dissipates) this step, never negative, and belongs to one LoadClass on one floor.
Facility power is the sum over all of them and IT power the sum over the IT equipment, so PUE
and every energy total come from the same step. The electrical network's sources supply the
facility power plus what the UPS batteries store (`storage_kw`), which is negative while they
carry the load.

Two domains share the work around the electrical network (`sim.electrical`). SiteLoadDomain
steps before it: until the building services (P4) are modelled, it is their stand-in,
working out each non-IT load from the state the other domains have reached (CRAC units, the
chiller plant, outdoor air) with simple curves, and nothing draws while its supply is dead.
The chiller plant (`sim.plant`) and the water network (`sim.water`) work out their own
equipment's draw. The electrical network adds the UPS and transformer losses, and
SitePowerDomain steps last to total everything up.
"""

import functools
from collections.abc import Mapping
from enum import StrEnum

from graphene_demo_twin.plant_design import PlantDesign
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
from graphene_demo_twin.sim.life_safety import FIRE_PUMP, LIFT, life_safety_power
from graphene_demo_twin.sim.placeholder import assets_of
from graphene_demo_twin.sim.plant import PLANT, plant_nodes
from graphene_demo_twin.sim.state import AssetState, WorldState
from graphene_demo_twin.sim.thermal import (
    CRAC_TYPE,
    air_suppliers,
    served_room,
    ua_kw_per_k,
)
from graphene_demo_twin.sim.water import WATER, water_nodes
from graphene_demo_twin.sim.weather import DAY_S, LOCAL_OFFSET_S, site_air, ytd_hours

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
    LIFT: LoadClass.OTHER,
    FIRE_PUMP: LoadClass.OTHER,
}
FAN_KW = {"PAHU": 4.4, "FCU": 0.9, "FWU": 1.2, "Ceiling Cooling Units": 3.0}
"""Fan power of the chilled-water air units (`sim.airside`), which run at fixed speed."""
CRAC_FAN_KW = 6.0
"""CRAC EC fan power at full speed; it follows the cube of speed."""
SERVICE_KW = {
    "CDU": 3.5,
    "IPS": 1.5,
    "RCMS": 2.0,
}
"""Average draw of equipment whose physics comes later (the CDU loop, the control room's
critical circuits)."""
GENSET_AUX_KW = 10.0
"""Jacket-water heaters and battery chargers of the six gensets, beyond Meter16."""
LIGHTING_W_M2 = {
    "hall": 1.2,
    "support": 5.4,
    "electrical": 3.0,
    "water": 2.4,
    "cooling": 2.4,
    "airside": 1.8,
    "core": 4.8,
    "corridor": 4.2,
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
        power: dict[str, float] = {}
        air = site_air(state, design)
        dry = air["dry_bulb_c"]

        # DX CRAC units: fans, and compressors rejecting to the outdoor air. A unit without
        # supply has already stopped.
        dx_cooling = 0.0
        for crac in assets_of(design, CRAC_TYPE):
            s = state.assets[crac]
            cooling = _crac_cooling_kw(state, design, crac)
            cop = (12.5 - 0.02 * (dry - 27.0)) * (
                1.0 - 0.5 * s.get("constraint.condenser_derate", 0.0)
            )
            compressor = cooling / cop if s["compressor_pct"] > 0.0 else 0.0
            power[crac] = CRAC_FAN_KW * (s["fan_pct"] / 100.0) ** 3 + compressor
            dx_cooling += cooling

        # Air units on chilled water: their fans, while they run. The chiller plant
        # (`sim.plant`) takes up the heat their coils remove and works out its own
        # equipment's draw.
        for unit, type_id in air_units(design).items():
            s = assets[unit]
            if s.get("running", True):
                power[unit] = FAN_KW[type_id] * (1.0 - s.get("constraint.fan_loss", 0.0))
            else:
                power[unit] = 0.0
        # The water network (`sim.water`) works out its own pumps' draw, and what the towers
        # lose to evaporation, drift and blowdown.
        plant = assets.get(PLANT)
        chw_load = plant["load_kw"] if plant is not None else 0.0
        water = assets.get(WATER)
        makeup = water["tower_loss_lps"] * 3600.0 if water is not None else 0.0

        # Building services, lighting room by room, and the draw beyond the meters with
        # nothing authored below them: fixed by the time of day.
        local = ctx.time + LOCAL_OFFSET_S
        hour = (local % DAY_S) / 3600.0
        office = (local // DAY_S + 3) % 7 < 5 and 8.0 <= hour < 18.0
        dark = hour < 7.0 or hour >= 19.0
        power.update(_scheduled_loads(design, office, dark))
        power.update(life_safety_power(state, design))  # lifts and fire pumps, by their state
        for node in services(design):  # a stopped service (a tripped CDU) draws nothing
            if not assets[node].get("running", True):
                power[node] = 0.0

        if net.order and net.order[0] in assets:
            for node, kw in power.items():
                assets[node]["power_kw"] = kw if assets[flag[node]]["live"] else 0.0
        else:  # a world without the electrical network: everything has supply
            for node, kw in power.items():
                assets[node]["power_kw"] = kw
        site = state.assets[SITE]
        site["chw_load_kw"] = chw_load
        site["chw_plant_kw"] = plant["plant_kw"] if plant is not None else 0.0
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
        # Each floor's energy per load class holds the year so far, like the IT registers.
        # `complete` runs until the state settles, so assign rather than accumulate.
        hours = ytd_hours(ctx.time)
        seeded: dict[str, float] = {}
        for node, key in _energy_keys(ctx.design):
            seeded[key] = seeded.get(key, 0.0) + state.assets[node]["power_kw"] * hours
        site.update(seeded)
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
        for key, q, tau in _AVERAGE_KEYS:
            site[key] += (site[q] - site[key]) * ctx.dt / tau

    def _totals(self, state: WorldState, design: PlantDesign) -> None:
        assets = state.assets
        totals = [0.0] * len(_CLASSES)
        for node, i in _class_index(design):
            totals[i] += assets[node]["power_kw"]
        site = assets[SITE]
        for key, kw in zip(_CLASS_KEYS, totals, strict=True):
            site[key] = kw
        site["facility_kw"] = sum(totals)
        site["cooling_elec_kw"] = (
            totals[_CLASSES.index(LoadClass.COOLING)]
            + totals[_CLASSES.index(LoadClass.HEAT_REJECTION)]
            + totals[_CLASSES.index(LoadClass.VENTILATION)]
        )
        site["transformer_loss_kw"] = sum(state.assets[i]["power_kw"] for i in incomers(design))
        site["utility_kw"] = sum(state.assets[i]["p_kw"] for i in incomers(design))
        net = network(design)
        site["storage_kw"] = sum(state.assets[u]["battery_kw"] for u in net.ups)
        site["ups_capacity_kw"] = _ups_capacity_kw(design)


_CLASSES = tuple(LoadClass)
_CLASS_KEYS = tuple(f"{cls}_kw" for cls in _CLASSES)
_AVERAGE_KEYS = tuple(
    (f"avg.{window}.{q}", q, tau) for window, tau in AVERAGE_WINDOWS.items() for q in AVERAGED
)
"""(running average, the site variable it follows, its time constant)."""


@functools.cache
def _class_index(design: PlantDesign) -> tuple[tuple[str, int], ...]:
    """`_classes` with each load class as its position in _CLASSES, which hashes faster."""
    return tuple((node, _CLASSES.index(cls)) for node, cls in _classes(design))


@functools.cache
def _ups_capacity_kw(design: PlantDesign) -> float:
    net = network(design)
    return sum(ups_rating(net, u)[0] for m in hall_ups(design).values() for u in m)


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


@functools.cache
def _scheduled_loads(design: PlantDesign, office: bool, dark: bool) -> dict[str, float]:
    """The draw of the building services, every room's lighting and the stand-ins beyond
    meters with nothing authored below them, in or out of office hours, by day or night."""
    power = {node: SERVICE_KW[type_id] for node, type_id in services(design).items()}
    for room in design.rooms.values():
        if room.outdoor:
            watts = OUTDOOR_W_M2 if dark else 0.0
        else:
            watts = LIGHTING_W_M2.get(room.kind, 5.0)
            if room.kind in OCCUPIED_KINDS and not office:
                watts *= 0.9
        power[room.id] = room.w * room.h * watts / 1000.0
    for meter in stand_in_meters(design):
        power[meter] = GENSET_AUX_KW
    return power


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
    owned = {*net.ups, *net.incomers, *net.tanks, *plant_nodes(design), *water_nodes(design)}
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
"""Site variables SiteLoadDomain owns: the cooling loads and the chiller plant's draw."""


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
