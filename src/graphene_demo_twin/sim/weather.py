"""Tropical weather at the site (Kuala Lumpur / Singapore class), as the roof station sees it.

The outdoor air follows a daily cycle, afternoon thunderstorms and a deterministic seasonal
drift, all drawn from the world's seeded noise, so it is a pure function of seed and sim time.
It is the outdoor boundary the plant rejects heat to: the towers see its wet bulb, the DX
condensers and fresh-air handlers its dry bulb and moisture. An external fault can make it
more humid than the Base World.
"""

import functools
import math
from dataclasses import dataclass

from graphene_demo_twin.plant_design import PlantDesign
from graphene_demo_twin.sim.engine import StepContext
from graphene_demo_twin.sim.events import Event
from graphene_demo_twin.sim.noise import Noise
from graphene_demo_twin.sim.state import AssetState, WorldState

WEATHER_TYPE = "Weather Station"
WEATHER_STATION = "~WX-01"
"""The roof weather station: an Unexported Asset, observed through
`Chiller System Control/Weather`."""
LOCAL_OFFSET_S = 8 * 3600
"""The site keeps UTC+8; daily and weekly cycles follow local time."""
HUMID_SPELL_TAU_S = 600.0
"""Time constant of a humid spell's excess wet bulb dispersing once its fault is cleared,
as drier air moves in."""
DAY_S = 86_400
YEAR_S = 365.2425 * DAY_S

DRY_BULB_C = (24.8, 33.0)
WET_BULB_C = (24.02, 26.98)
RH_PCT = (60.0, 95.0)
"""The climate's bounds (Plant Design basis), kept just inside so rounding never leaves them."""

COMPASS = ("N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE") + (
    "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
)  # fmt: skip


@dataclass(frozen=True, slots=True)
class OutdoorAir:
    dry_bulb_c: float
    dew_point_c: float
    wet_bulb_c: float
    rh_pct: float
    pressure_hpa: float
    wind_mps: float
    wind_dir_deg: float
    rain_mmph: float


# ---- Psychrometrics (Magnus saturation pressure, ASHRAE wet-bulb relation)


def saturation_kpa(t_c: float) -> float:
    return 0.61094 * math.exp(17.625 * t_c / (t_c + 243.04))


def dew_point_of(vapour_kpa: float) -> float:
    g = math.log(vapour_kpa / 0.61094)
    return 243.04 * g / (17.625 - g)


def relative_humidity(dry_bulb_c: float, dew_point_c: float) -> float:
    return 100.0 * saturation_kpa(dew_point_c) / saturation_kpa(dry_bulb_c)


def dew_point_at(dry_bulb_c: float, rh_pct: float) -> float:
    return dew_point_of(rh_pct / 100.0 * saturation_kpa(dry_bulb_c))


def _humidity_ratio(vapour_kpa: float, p_kpa: float) -> float:
    return 0.621945 * vapour_kpa / (p_kpa - vapour_kpa)


def _psychrometric_ratio(dry_bulb_c: float, wet_bulb_c: float, p_kpa: float) -> float:
    """Humidity ratio of air at `dry_bulb_c` whose adiabatic saturation temperature is
    `wet_bulb_c`."""
    ws = _humidity_ratio(saturation_kpa(wet_bulb_c), p_kpa)
    return ((2501.0 - 2.326 * wet_bulb_c) * ws - 1.006 * (dry_bulb_c - wet_bulb_c)) / (
        2501.0 + 1.86 * dry_bulb_c - 4.186 * wet_bulb_c
    )


def wet_bulb(dry_bulb_c: float, dew_point_c: float, pressure_hpa: float) -> float:
    """Thermodynamic wet bulb, solved by bisection between dew point and dry bulb."""
    p = pressure_hpa / 10.0
    w = _humidity_ratio(saturation_kpa(dew_point_c), p)
    lo, hi = dew_point_c, dry_bulb_c
    for _ in range(48):
        mid = 0.5 * (lo + hi)
        if _psychrometric_ratio(dry_bulb_c, mid, p) < w:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def dew_point_for_wet_bulb(dry_bulb_c: float, wet_bulb_c: float, pressure_hpa: float) -> float:
    """The dew point that gives air at `dry_bulb_c` the wet bulb `wet_bulb_c`."""
    p = pressure_hpa / 10.0
    w = _psychrometric_ratio(dry_bulb_c, wet_bulb_c, p)
    return dew_point_of(w * p / (0.621945 + w))


def enthalpy_kj_per_kg(dry_bulb_c: float, dew_point_c: float, pressure_hpa: float) -> float:
    w = _humidity_ratio(saturation_kpa(dew_point_c), pressure_hpa / 10.0)
    return 1.006 * dry_bulb_c + w * (2501.0 + 1.86 * dry_bulb_c)


# ---- The weather itself


def outdoor_air(noise: Noise, time: int) -> OutdoorAir:
    """The Base World's outdoor air at `time`; a pure function of the seed and sim time."""
    local = time + LOCAL_OFFSET_S
    hour = (local % DAY_S) / 3600.0
    day = local // DAY_S
    year = (local % YEAR_S) / YEAR_S
    hot_season = math.sin(2.0 * math.pi * (year - 0.08))  # warmest and most humid in May
    sw_monsoon = math.sin(2.0 * math.pi * (year - 0.3))  # May to October

    storm, after = _storm(noise, day, hour, sw_monsoon)
    rain = storm * (8.0 + 35.0 * noise.held("wx/storm/mm", day))
    cloud = noise.smooth("wx/cloud", time, DAY_S)
    swing = 3.4 * (1.0 + 0.2 * cloud) * math.cos(2.0 * math.pi * (hour - 14.5) / 24.0)
    dry = (
        28.4
        + 0.6 * hot_season
        + 0.5 * noise.smooth("wx/db", time, 6 * 3600)
        + swing
        - 4.0 * max(storm, after)
    )
    dew = 24.35 + 0.35 * hot_season + 0.35 * noise.smooth("wx/dp", time, 4 * 3600) + 0.3 * storm
    pressure = (
        1009.5
        + 1.1 * math.cos(4.0 * math.pi * (hour - 10.0) / 24.0)
        + 1.8 * noise.smooth("wx/p", time, DAY_S)
    )

    dry = _soft_limit(dry, *DRY_BULB_C, knee=1.0)
    dew = dew_point_at(dry, _soft_limit(relative_humidity(dry, dew), *RH_PCT, knee=4.0))
    wb = wet_bulb(dry, dew, pressure)
    if not WET_BULB_C[0] <= wb <= WET_BULB_C[1]:
        wb = min(max(wb, WET_BULB_C[0]), WET_BULB_C[1])
        dew = dew_point_for_wet_bulb(dry, wb, pressure)
        wb = wet_bulb(dry, dew, pressure)

    sea_breeze = max(0.0, math.cos(2.0 * math.pi * (hour - 15.0) / 24.0))
    wind = 1.2 + 1.6 * sea_breeze + 0.8 * (1.0 + noise.smooth("wx/wind", time, 7200)) + 5.0 * storm
    southwest = 0.5 + 0.5 * math.tanh(3.0 * sw_monsoon)
    direction = 45.0 + 180.0 * southwest + 35.0 * noise.smooth("wx/dir", time, 3 * 3600)
    return OutdoorAir(
        dry_bulb_c=dry,
        dew_point_c=dew,
        wet_bulb_c=wb,
        rh_pct=relative_humidity(dry, dew),
        pressure_hpa=pressure,
        wind_mps=wind,
        wind_dir_deg=direction % 360.0,
        rain_mmph=rain,
    )


def _storm(noise: Noise, day: int, hour: float, sw_monsoon: float) -> tuple[float, float]:
    """An afternoon thunderstorm on some days: its intensity now in [0, 1], and the cooling
    left behind after it has passed, in [0, 1]."""
    if noise.held("wx/storm", day) >= 0.45 + 0.15 * sw_monsoon:
        return 0.0, 0.0
    start = 13.5 + 4.0 * noise.held("wx/storm/at", day)
    length = 0.75 + 1.25 * noise.held("wx/storm/h", day)
    into = hour - start
    if into < 0.0:
        return 0.0, 0.0
    if into < length:
        return math.sin(math.pi * into / length), 0.0
    return 0.0, math.exp(-(into - length) / 1.5) * 0.6


def _soft_limit(x: float, lo: float, hi: float, knee: float) -> float:
    """`x`, eased into [lo, hi] within `knee` of either bound instead of clipped."""
    if x > hi - knee:
        return hi - knee + knee * math.tanh((x - hi + knee) / knee)
    if x < lo + knee:
        return lo + knee - knee * math.tanh((lo + knee - x) / knee)
    return x


def compass(degrees: float) -> str:
    return COMPASS[round(degrees / 22.5) % 16]


def with_wet_bulb_rise(air: OutdoorAir, rise_c: float) -> OutdoorAir:
    """The same air made more humid, so its wet bulb is `rise_c` higher; its dry bulb is
    unchanged, so the wet bulb stops at the dry bulb (saturated air)."""
    target = min(air.wet_bulb_c + rise_c, air.dry_bulb_c)
    dry = air.dry_bulb_c
    dew = target if target >= dry else dew_point_for_wet_bulb(dry, target, air.pressure_hpa)
    return OutdoorAir(
        dry_bulb_c=dry,
        dew_point_c=dew,
        wet_bulb_c=target,
        rh_pct=relative_humidity(dry, dew),
        pressure_hpa=air.pressure_hpa,
        wind_mps=air.wind_mps,
        wind_dir_deg=air.wind_dir_deg,
        rain_mmph=air.rain_mmph,
    )


class WeatherDomain:
    """The outdoor air at the roof weather station, and the rain it has seen today."""

    settling_s = int(6 * HUMID_SPELL_TAU_S)

    def initial(self, ctx: StepContext) -> dict[str, AssetState]:
        local = ctx.time + LOCAL_OFFSET_S
        midnight = ctx.time - local % DAY_S
        rain = sum(
            outdoor_air(ctx.noise, t).rain_mmph * 60.0 / 3600.0
            for t in range(midnight, ctx.time, 60)
        )
        return {
            node: {**_air_state(outdoor_air(ctx.noise, ctx.time)), "rain_today_mm": rain}
            for node in stations(ctx.design)
        }

    def handles(self, event: Event, design: PlantDesign) -> bool:
        return False

    def apply(self, event: Event, state: WorldState) -> None:
        raise AssertionError("no events")

    def step(self, state: WorldState, ctx: StepContext) -> None:
        air = outdoor_air(ctx.noise, ctx.time)
        new_day = (ctx.time + LOCAL_OFFSET_S) % DAY_S == 0
        for node in stations(ctx.design):
            s = state.assets[node]
            rise = _humid_spell(s, ctx.dt)
            here = with_wet_bulb_rise(air, rise) if rise > 0.0 else air
            s.update(_air_state(here))
            s["rain_today_mm"] = (0.0 if new_day else s["rain_today_mm"]) + (
                here.rain_mmph * ctx.dt / 3600.0
            )


def _humid_spell(s: AssetState, dt: int) -> float:
    """The wet-bulb rise the humid spell gives the air now. It follows the fault's
    constraint up; once that falls (or is cleared) the humid air disperses with
    `HUMID_SPELL_TAU_S` rather than vanishing."""
    constraint = s.get("constraint.wet_bulb_rise_c", 0.0)
    spell = s.get("humid_spell_c", 0.0)
    if constraint >= spell:
        spell = constraint
    else:
        spell = constraint + (spell - constraint) * math.exp(-dt / HUMID_SPELL_TAU_S)
        if spell - constraint < 1e-6:
            spell = constraint
    s["humid_spell_c"] = spell
    return spell


def _air_state(air: OutdoorAir) -> AssetState:
    return {
        "dry_bulb_c": air.dry_bulb_c,
        "dew_point_c": air.dew_point_c,
        "wet_bulb_c": air.wet_bulb_c,
        "rh_pct": air.rh_pct,
        "pressure_hpa": air.pressure_hpa,
        "wind_mps": air.wind_mps,
        "wind_dir_deg": air.wind_dir_deg,
        "rain_mmph": air.rain_mmph,
        "status": "NORMAL",
    }


@functools.cache
def stations(design: PlantDesign) -> tuple[str, ...]:
    return tuple(a.path for a in design.assets.values() if a.type_id == WEATHER_TYPE and a.room)


def site_air(state: WorldState, design: PlantDesign) -> AssetState:
    """The outdoor air the site sees: its first weather station's state."""
    return state.assets[stations(design)[0]]
