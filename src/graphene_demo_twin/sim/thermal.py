"""Thermal zones: each Data Hall, and each support room with airside units, is its own air
volume with first-order thermal inertia, and the sensors placed in it observe it.

Heat in is everything that dissipates in the room: a hall's IT Load, a UPS room's module
losses, a switch room's transformer losses, the control room's circuits and the load its UPS
carries, and every room's lighting. Heat out is the cooling its air suppliers (its upstream
`air` connections in the Plant Design) actually deliver: each removes heat in proportion to its
airflow and to how far the room's return air sits above the air it supplies. Zones exchange
heat only through those connections; the Plant Design joins no zone to another, so every zone
is independent.

Moisture: the fresh-air handlers serving a zone hold its dew point low, a little above it on
humid days; without one running it drifts towards the outdoor dew point. Relative humidity
follows from the dew point at each temperature.

A chilled-water air unit (PAHU, FCU, FWU, CDU) that the airside (#22) does not model yet
runs at full airflow whenever it has supply, and its coil cools the air towards the water
the chiller plant (`sim.plant`) supplies: SUPPLY_C at the design supply temperature and full
flow, warmer as the water warms or the flow falls short. The DX CRAC units are modelled in
`sim.placeholder`.
"""

import functools
import math
from dataclasses import dataclass

from graphene_demo_twin.plant_design import ConnectionKind, PlantDesign
from graphene_demo_twin.sim.electrical import network, powered
from graphene_demo_twin.sim.engine import StepContext
from graphene_demo_twin.sim.events import Event
from graphene_demo_twin.sim.it_load import it_equipment, it_in, it_utilisation
from graphene_demo_twin.sim.state import AssetState, WorldState
from graphene_demo_twin.sim.weather import outdoor_air, saturation_kpa, site_air

SUPPLY_C = 18.0
"""Supply air temperature every air supplier delivers at steady state."""
PLANT = "~PLC-01"
"""World-state key of the chiller plant (`sim.plant`) as a whole: its headers, its
Controllers and its totals. It is the plant Controller's Unexported Asset, which takes the
supervisory Operator Commands."""
COIL_APPROACH_K = 4.0
"""How far a chilled-water coil's leaving air sits above the water supplied to it."""
HALL_C = 24.0
"""Data Hall return air temperature at its nominal IT Load with full cooling."""
NOMINAL_UTILISATION = 0.675
"""Share of design IT Load at which a hall sits at HALL_C with full cooling."""
SUPPORT_DESIGN_KW = 150.0
"""Heat a support room's air suppliers remove at full flow with its air at HALL_C."""

CRAC_TYPE = "CRAC"
FRESH_AIR_TYPE = "PAHU"
AIR_UNIT_TYPES = frozenset({CRAC_TYPE, "PAHU", "FCU", "FWU", "CDU", "Ceiling Cooling Units"})
"""Cooling equipment: what it draws leaves with the refrigerant or the chilled water, not
as heat in the room it stands in."""
AIR_WEIGHT = {CRAC_TYPE: 3.0}
"""Share of a zone's cooling per air supplier type, relative to 1 for any other supplier."""

HOT_AISLE_TYPE = "Temperature and Humidity"
COLD_AISLE_TYPE = "Environment Monitoring"
COLD_AISLE_SPREAD_C = 0.6
"""Largest difference between one cold-aisle spot and the aisle's mean."""
HOT_AISLE_SPREAD_C = 0.8
"""Largest difference between one hot-aisle spot and the hall's return air."""
DRIFT_LIMIT_C = 15.0
"""Furthest a drifting temperature element wanders before it saturates."""


class ThermalZoneDomain:
    """Every zone's air temperature, its heat balance and the moisture in it. It steps after
    the electrical network, so a step's heat is what every load dissipated in that step."""

    TAU_S = 600.0
    """Thermal time constant with full cooling."""
    DEW_TAU_S = TAU_S
    DEW_POINT_C = 11.0
    """Dew point the fresh-air handlers hold on a day with a 24.35 °C outdoor dew point."""
    DEW_LEAK = 0.2
    """How much of an outdoor dew point change leaks past the fresh-air handlers."""
    RECIRCULATION = 0.15
    """Share of hot-aisle air that reaches the cold aisle at full airflow. The IT fans pull
    the rest of what they need from the hot aisle when the air suppliers deliver less."""
    settling_s = int(8 * TAU_S)
    """Time for a disturbance to die out to within 0.03 %: losing every air unit on a bus
    takes a hall far from its steady state."""

    @staticmethod
    def capacity_kj_per_k(design: PlantDesign, zone: str) -> float:
        """Heat that warms the zone's air (and what it stands on) by one kelvin."""
        return ua_kw_per_k(design, zone) * ThermalZoneDomain.TAU_S

    def initial(self, ctx: StepContext) -> dict[str, AssetState]:
        """Each zone at the temperature its IT Load alone would hold it at; `complete` then
        balances it against every other heat source."""
        dew = self._dew_target(outdoor_air(ctx.noise, ctx.time).dew_point_c)
        states = {}
        for zone in zones(ctx.design):
            it = math.fsum(
                b.design_kw * it_utilisation(ctx.noise, b, ctx.time)
                for n, b in it_equipment(ctx.design).items()
                if ctx.design.asset(n).room == zone
            )
            temp = SUPPLY_C + it / ua_kw_per_k(ctx.design, zone) if it else HALL_C
            states[zone] = {
                "it_heat_kw": it,
                "heat_kw": it,
                "cooling_kw": it,
                "delivered_fraction": 1.0,
                "temp_c": temp,
                "cold_aisle_c": SUPPLY_C + self.RECIRCULATION * (temp - SUPPLY_C),
                "dew_point_c": dew,
                "rh_pct": relative_humidity_pct(temp, dew),
            }
        return states

    def complete(self, state: WorldState, ctx: StepContext) -> None:
        for zone in _zone_plan(ctx.design):
            s = state.assets[zone.id]
            heat, it = _heat(state, zone)
            flow, supplied = _supply(state, zone)
            if flow > 0.0:
                s["temp_c"] = (heat / zone.ua + supplied) / flow
            self._observe(s, heat, it, heat, flow, supplied)

    def handles(self, event: Event, design: PlantDesign) -> bool:
        return False

    def apply(self, event: Event, state: WorldState) -> None:
        raise AssertionError("no events")

    def step(self, state: WorldState, ctx: StepContext) -> None:
        outdoor = site_air(state, ctx.design)["dew_point_c"]
        target = self._dew_target(outdoor)
        blend = ctx.dt / self.DEW_TAU_S
        for zone in _zone_plan(ctx.design):
            s = state.assets[zone.id]
            heat, it = _heat(state, zone)
            flow, supplied = _supply(state, zone)
            cooling = zone.ua * (flow * s["temp_c"] - supplied)
            s["temp_c"] += (heat - cooling) * ctx.dt / zone.capacity
            dew = s["dew_point_c"]
            towards = target if _dehumidified(state, zone) else outdoor
            s["dew_point_c"] = dew + (towards - dew) * blend
            self._observe(s, heat, it, cooling, flow, supplied)

    def _observe(
        self, s: AssetState, heat: float, it: float, cooling: float, flow: float, supplied: float
    ) -> None:
        """The zone's derived variables, from its temperature and this step's air supply."""
        temp = s["temp_c"]
        mixed = supplied / flow if flow > 0.0 else temp
        recirculation = 1.0 - (1.0 - self.RECIRCULATION) * min(flow, 1.0)
        s["it_heat_kw"] = it
        s["heat_kw"] = heat
        s["cooling_kw"] = cooling
        # The zone's cooling demand is the heat it takes in: what the airside must remove to
        # hold it where it is. Pulling stored heat back out delivers more than that.
        s["delivered_fraction"] = min(max(cooling / heat, 0.0), 1.0) if heat > 0.0 else 1.0
        s["cold_aisle_c"] = mixed + recirculation * (temp - mixed)
        s["rh_pct"] = relative_humidity_pct(temp, s["dew_point_c"])

    def _dew_target(self, outdoor_dew_c: float) -> float:
        return self.DEW_POINT_C + self.DEW_LEAK * (outdoor_dew_c - 24.35)


class ZoneSensorDomain:
    """The sensors that observe each Data Hall. Environment Monitoring sensors read the cold
    aisle and Temperature and Humidity sensors the hot aisle (return air), each at its own
    spot, which runs warmer or cooler than the mean by a bounded amount fixed by where it
    hangs. Sensor faults corrupt only the reading: an offset, a drift that grows until the
    fault is cleared (which recalibrates the element), or a reading stuck where it was."""

    settling_s = 0

    def initial(self, ctx: StepContext) -> dict[str, AssetState]:
        sensors = {**cold_aisle_sensors(ctx.design), **hot_aisle_sensors(ctx.design)}
        return {s: {"temp_c": HALL_C, "rh_pct": 50.0, "drift_c": 0.0} for s in sensors}

    def complete(self, state: WorldState, ctx: StepContext) -> None:
        self.step(state, ctx)

    def handles(self, event: Event, design: PlantDesign) -> bool:
        return False

    def apply(self, event: Event, state: WorldState) -> None:
        raise AssertionError("no events")

    def step(self, state: WorldState, ctx: StepContext) -> None:
        assets = state.assets
        for hall, aisles in _sensor_plan(ctx.design):
            h = assets[hall]
            dew = h["dew_point_c"]
            vapour = _MAGNUS_A * dew / (dew + _MAGNUS_B)
            for aisle, sensors in aisles:
                mean = h[aisle]
                for sensor, spot in sensors:
                    s = assets[sensor]
                    true_c = mean + spot
                    if s["drift_c"] or not _OBSERVATIONS.isdisjoint(s):
                        if not self._corrupt(s, true_c, ctx.dt):
                            continue  # frozen on its last reading
                    else:
                        s["temp_c"] = true_c
                    s["rh_pct"] = min(
                        100.0 * math.exp(vapour - _MAGNUS_A * true_c / (true_c + _MAGNUS_B)), 100.0
                    )

    @staticmethod
    def _corrupt(s: AssetState, true_c: float, dt: float) -> bool:
        """Take a faulted sensor's reading of `true_c`; False if it is stuck."""
        rate = s.get("observation.drift_c_per_h", 0.0)
        drifted = s["drift_c"] + rate * dt / 3600.0 if rate > 0.0 else 0.0
        s["drift_c"] = min(drifted, DRIFT_LIMIT_C)
        if s.get("observation.stuck", 0.0) >= 0.5:
            return False
        s["temp_c"] = true_c + s.get("observation.offset_c", 0.0) + s["drift_c"]
        return True


_MAGNUS_A, _MAGNUS_B = 17.625, 243.04
"""The Magnus coefficients of `weather.saturation_kpa`, for the ratio of two saturation
pressures in one exponential."""

_OBSERVATIONS = frozenset(
    {"observation.drift_c_per_h", "observation.stuck", "observation.offset_c"}
)
"""The observation variables sensor faults drive."""


@functools.cache
def _sensor_plan(
    design: PlantDesign,
) -> tuple[tuple[str, tuple[tuple[str, tuple[tuple[str, float], ...]], ...]], ...]:
    """Hall → ((its cold-aisle mean, its sensors and their spots), (return air, ...))."""
    plan = []
    for hall in halls(design):
        aisles = tuple(
            (aisle, tuple((s, spot) for s, (h, spot) in sensors.items() if h == hall))
            for sensors, aisle in (
                (cold_aisle_sensors(design), "cold_aisle_c"),
                (hot_aisle_sensors(design), "temp_c"),
            )
        )
        plan.append((hall, aisles))
    return tuple(plan)


def relative_humidity_pct(temp_c: float, dew_point_c: float) -> float:
    return min(100.0 * saturation_kpa(dew_point_c) / saturation_kpa(temp_c), 100.0)


def zone_heat_kw(state: WorldState, design: PlantDesign, zone: str) -> float:
    """Heat dissipated in `zone` this step: everything its heat sources draw."""
    assets = state.assets
    return sum(assets[n][key] for n, key in heat_sources(design, zone) if key in assets.get(n, ()))


def supplier_air(state: WorldState, design: PlantDesign, supplier: str) -> tuple[float, float]:
    """(airflow as a fraction of nominal, supply temperature) of an air supplier. A unit not
    modelled yet runs at full flow whenever it has supply, its coil cooling its room's air
    with the chilled water the plant supplies (`chw_coil_air`)."""
    unit = state.assets.get(supplier)
    if unit is not None and "airflow" in unit:
        return unit["airflow"], unit["supply_c"]
    room = served_room(design, supplier)
    return_c = state.assets[room]["temp_c"] if room in state.assets else HALL_C
    supply_c = chw_coil_air(state.assets.get(PLANT), return_c)
    return (1.0 if powered(state, design, supplier) else 0.0), supply_c


def chw_coil_air(plant: AssetState | None, return_c: float) -> float:
    """Air leaving a chilled-water coil that takes in air at `return_c`: COIL_APPROACH_K above
    the plant's supply water with the flow its valve asks for, and less cooled by the share
    of that flow the plant does not deliver. SUPPLY_C in a world without the plant."""
    if plant is None:
        return SUPPLY_C
    coil = plant["chws_c"] + COIL_APPROACH_K
    if return_c <= coil:
        return return_c
    return return_c - (return_c - coil) * plant["delivery"]


def dehumidified(state: WorldState, design: PlantDesign, zone: str) -> bool:
    """Whether a fresh-air handler is drying the zone's air; a zone with none is held dry."""
    units = fresh_air_units(design, zone)
    return not units or any(powered(state, design, u) for u in units)


@dataclass(frozen=True, slots=True)
class _Zone:
    """What a zone's step needs from the Plant Design, worked out once."""

    id: str
    ua: float
    capacity: float
    heat: tuple[tuple[str, str], ...]
    it: tuple[str, ...]
    supply: tuple[tuple[str, float, str | None], ...]
    """(supplier, its share, the node whose `live` says whether it has supply)."""
    fresh_air: tuple[str | None, ...]
    """The supply flags of its fresh-air handlers."""


@functools.cache
def _zone_plan(design: PlantDesign) -> tuple[_Zone, ...]:
    flags = network(design).supply_flag
    return tuple(
        _Zone(
            zone,
            ua_kw_per_k(design, zone),
            ThermalZoneDomain.capacity_kj_per_k(design, zone),
            heat_sources(design, zone),
            it_in(design, zone),
            tuple((n, share, flags.get(n)) for n, share in air_suppliers(design, zone)),
            tuple(flags.get(n) for n in fresh_air_units(design, zone)),
        )
        for zone in zones(design)
    )


def _heat(state: WorldState, zone: _Zone) -> tuple[float, float]:
    """(`zone_heat_kw`, the IT Load's share of it)."""
    assets = state.assets
    heat = 0.0
    for node, key in zone.heat:
        s = assets.get(node)
        if s is not None and key in s:
            heat += s[key]
    it = 0.0
    for node in zone.it:
        it += assets[node]["power_kw"]
    return heat, it


def _live(state: WorldState, flag: str | None) -> bool:
    """`electrical.powered`, given the node's supply flag."""
    return flag is None or state.assets.get(flag, _LIVE).get("live", True)


def _dehumidified(state: WorldState, zone: _Zone) -> bool:
    return not zone.fresh_air or any(_live(state, f) for f in zone.fresh_air)


def _supply(state: WorldState, zone: _Zone) -> tuple[float, float]:
    """(Σ share × airflow, Σ share × airflow × supply temperature) over the zone's suppliers;
    `supplier_air` over each."""
    assets = state.assets
    flow = supplied = 0.0
    chw_c = chw_coil_air(assets.get(PLANT), assets[zone.id]["temp_c"])
    for supplier, share, flag in zone.supply:
        unit = assets.get(supplier)
        if unit is not None and "airflow" in unit:
            airflow, supply_c = unit["airflow"], unit["supply_c"]
        elif _live(state, flag):
            airflow, supply_c = 1.0, chw_c
        else:
            continue
        flow += share * airflow
        supplied += share * airflow * supply_c
    return flow, supplied


_LIVE: AssetState = {}


# ---- Plant Design lookups (derived once per design; the design is immutable)


@functools.cache
def halls(design: PlantDesign) -> tuple[str, ...]:
    return tuple(r.id for r in design.rooms.values() if r.kind == "hall")


@functools.cache
def zones(design: PlantDesign) -> tuple[str, ...]:
    """Every Data Hall, and every other room with air suppliers, in authored order."""
    return tuple(
        r.id
        for r in design.rooms.values()
        if r.kind == "hall" or design.upstream(r.id, ConnectionKind.AIR)
    )


@functools.cache
def air_suppliers(design: PlantDesign, room: str) -> tuple[tuple[str, float], ...]:
    """The units supplying air to `room` and each one's share of its cooling."""
    suppliers = design.upstream(room, ConnectionKind.AIR)
    weights = [AIR_WEIGHT.get(_type_of(design, n), 1.0) for n in suppliers]
    total = sum(weights)
    return tuple((n, w / total) for n, w in zip(suppliers, weights, strict=True))


@functools.cache
def fresh_air_units(design: PlantDesign, zone: str) -> tuple[str, ...]:
    return tuple(n for n, _ in air_suppliers(design, zone) if _type_of(design, n) == FRESH_AIR_TYPE)


@functools.cache
def served_room(design: PlantDesign, unit: str) -> str | None:
    rooms = [n for n in design.downstream(unit, ConnectionKind.AIR) if design.is_room(n)]
    return rooms[0] if rooms else None


@functools.cache
def heat_sources(design: PlantDesign, zone: str) -> tuple[tuple[str, str], ...]:
    """(node, the variable holding the heat it dissipates) for the zone itself (its lighting)
    and every load on the power graph placed in it, except its cooling equipment and the
    meters whose stand-in draw is spent elsewhere. A UPS module dissipates its losses: what
    it draws to charge its battery is stored, and what it delivers is spent where its load
    is. A UPS with no branch below it (the control UPS) carries a load in its own room, so
    what it delivers is spent there too."""
    net = network(design)
    stand_ins = {f.meter for f in net.feeds if f.stand_in}
    powered_nodes = {*net.supply_flag, *net.it, *net.ups, *net.incomers}
    sources: list[tuple[str, str]] = [(zone, "power_kw")]
    for a in design.assets_in(zone):
        if a.path not in powered_nodes or a.type_id in AIR_UNIT_TYPES or a.path in stand_ins:
            continue
        if a.path not in net.ups_branch:
            sources.append((a.path, "power_kw"))
            continue
        sources.append((a.path, "loss_kw"))
        if net.ups_branch[a.path] is None:
            sources.append((a.path, "output_kw"))
    return tuple(sources)


@functools.cache
def ua_kw_per_k(design: PlantDesign, zone: str) -> float:
    """Heat a zone's air suppliers remove per kelvin of its air above supply, at full flow."""
    basis = design.it_basis.get(zone)
    nominal = SUPPORT_DESIGN_KW if basis is None else basis.design_kw * NOMINAL_UTILISATION
    return nominal / (HALL_C - SUPPLY_C)


@functools.cache
def cold_aisle_sensors(design: PlantDesign) -> dict[str, tuple[str, float]]:
    """Environment Monitoring sensor → the Data Hall it sits in and how much warmer its spot
    in the cold aisle runs than the aisle's mean, fixed by where it is placed: supply air
    enters the aisles from the hall's east wall, where its air units stand, and warms (mixing
    with recirculated air) towards the far, west end."""
    return {
        path: (hall, COLD_AISLE_SPREAD_C * (2.0 * along - 1.0))
        for path, hall, along, _ in _placed(design, COLD_AISLE_TYPE)
    }


@functools.cache
def hot_aisle_sensors(design: PlantDesign) -> dict[str, tuple[str, float]]:
    """Temperature and Humidity sensor → the Data Hall it sits in and how much warmer its
    spot in the hot aisle runs than the hall's return air: warmest far from the air units,
    where the return path is longest, and a little warmer towards the hall's south side."""
    return {
        path: (hall, HOT_AISLE_SPREAD_C * (0.7 * (2.0 * along - 1.0) + 0.3 * (2.0 * across - 1.0)))
        for path, hall, along, across in _placed(design, HOT_AISLE_TYPE)
    }


def _placed(design: PlantDesign, type_id: str) -> list[tuple[str, str, float, float]]:
    """(sensor, hall, distance from the air-unit wall, distance from the north wall), both
    as fractions of the hall, for every sensor of `type_id` in a Data Hall."""
    in_halls = set(halls(design))
    found = []
    for a in design.assets.values():
        if a.type_id != type_id or a.room not in in_halls:
            continue
        room = design.room(a.room)
        along = _clamp((room.x + room.w - a.x) / room.w)
        across = _clamp((a.y - room.y) / room.h)
        found.append((a.path, a.room, along, across))
    return found


def _clamp(x: float) -> float:
    return min(max(x, 0.0), 1.0) if math.isfinite(x) else 0.5


def _type_of(design: PlantDesign, node: str) -> str:
    placed = design.assets.get(node)
    return "" if placed is None else placed.type_id
