"""Point bindings for the site as a whole: weather, IT Load and the KPIs derived from them.

`Chiller System Control/Weather` observes the roof weather station. The `Dashboard` and `Other`
Plant Views observe the site's power flow: every figure is a sum or ratio over the same
step's consumer powers and energy integrals, so PUE, loads and the energy breakdown can never
disagree with each other.
"""

from collections.abc import Callable, Iterable

from graphene_demo_twin.asset_model import AssetModel
from graphene_demo_twin.plant_design import PlantDesign
from graphene_demo_twin.projection.projector import Binding
from graphene_demo_twin.sim import Scalar, WorldState
from graphene_demo_twin.sim.it_load import it_equipment
from graphene_demo_twin.sim.site import (
    KW_PER_RT,
    SITE,
    LoadClass,
    consumers,
    energy_key,
)
from graphene_demo_twin.sim.weather import compass, stations

WEATHER = "Chiller System Control/Weather"
DASHBOARD = "Dashboard"
ENERGY = "Dashboard/Energy"
OTHER = "Other"

WEATHER_POINTS: dict[str, Callable[[dict[str, Scalar]], Scalar]] = {
    "Outside Temperature": lambda s: s["dry_bulb_c"],
    "Wet Bulb Temperature": lambda s: s["wet_bulb_c"],
    "Dew Point": lambda s: s["dew_point_c"],
    "Outside Humidity": lambda s: s["rh_pct"],
    "Outside Pressure": lambda s: s["pressure_hpa"],
    "Wind Speed": lambda s: s["wind_mps"],
    "Wind Direction": lambda s: s["wind_dir_deg"],
    "Wind Direction Text": lambda s: compass(s["wind_dir_deg"]),
    "Total Precipitation": lambda s: s["rain_today_mm"],
    "Station Status": lambda s: s["status"],
}
"""Weather member → its value from the weather station's AssetState."""

BREAKDOWN = {
    "Central Cooling": (LoadClass.COOLING,),
    "Heat Rejection": (LoadClass.HEAT_REJECTION,),
    "Ventilation": (LoadClass.VENTILATION,),
    "Office Lighting": (LoadClass.LIGHTING,),
    "Others": (LoadClass.LOSSES, LoadClass.OTHER),
}
"""Dashboard energy breakdown → the load classes it sums; with IT energy it is the total."""

ROLLING = {"Daily": "day", "Monthly": "month", "Annually": "year"}


def site_bindings(asset_model: AssetModel, design: PlantDesign) -> list[Binding]:
    bindings = [
        *_weather(design),
        *_it_load(asset_model, design),
        *_dashboard(design),
        *_other(asset_model),
    ]
    missing = [b.path for b in bindings if b.path not in asset_model.points]
    if missing:
        raise ValueError(f"site bindings name points not in the Asset Model: {missing}")
    return bindings


def _weather(design: PlantDesign) -> Iterable[Binding]:
    station = stations(design)[0]
    for member, read in WEATHER_POINTS.items():
        yield Binding(f"{WEATHER}/{member}", _read(station, read))


def _it_load(asset_model: AssetModel, design: PlantDesign) -> Iterable[Binding]:
    """Each hall's IT Load as its Environment Monitoring aggregate sees it, and its IT energy
    on the Dashboard. Its branch circuit monitors are the electrical network's."""
    for node in it_equipment(design):
        hall = design.room_of(node)
        yield Binding(
            f"Environment Monitoring/{hall.floor}/{hall.id}/IT Load", _var(node, "power_kw")
        )
        yield Binding(
            f"{ENERGY}/Floors/{hall.floor}/Data Halls/{hall.id}/IT Energy",
            _var(node, "energy_kwh"),
        )


def _dashboard(design: PlantDesign) -> Iterable[Binding]:
    floors = [f.name for f in design.floors]
    it_on = {f: [n for n in it_equipment(design) if design.room_of(n).floor == f] for f in floors}
    buckets = {
        f: [
            energy_key(f, cls)
            for cls in LoadClass
            if cls is not LoadClass.IT and (cls, f) in set(consumers(design).values())
        ]
        for f in floors
    }

    def it_energy(floor: str) -> Callable[[WorldState], float]:
        nodes = it_on[floor]
        return lambda s: sum(s.assets[n]["energy_kwh"] for n in nodes)

    def non_it_energy(floor: str) -> Callable[[WorldState], float]:
        keys = buckets[floor]
        return lambda s: sum(s.assets[SITE][k] for k in keys)

    def floor_energy(floor: str) -> Callable[[WorldState], float]:
        it, other = it_energy(floor), non_it_energy(floor)
        return lambda s: it(s) + other(s)

    def building_energy(s: WorldState) -> float:
        return sum(floor_energy(f)(s) for f in floors)

    def class_energy(classes: tuple[LoadClass, ...]) -> Callable[[WorldState], float]:
        keys = [energy_key(f, c) for f in floors for c in classes if energy_key(f, c) in buckets[f]]
        return lambda s: sum(s.assets[SITE][k] for k in keys)

    for floor in floors:
        yield Binding(f"{ENERGY}/Floors/{floor}/Total Energy", floor_energy(floor))
        if it_on[floor]:
            yield Binding(f"{ENERGY}/Floors/{floor}/Data Halls/IT Energy", it_energy(floor))
            yield Binding(f"{ENERGY}/Floors/{floor}/Non-IT Energy", non_it_energy(floor))
    yield Binding(f"{ENERGY}/Building/Total Energy", building_energy)
    yield Binding(
        f"{ENERGY}/Building/Total IT Energy", lambda s: sum(it_energy(f)(s) for f in floors)
    )
    yield Binding(f"{DASHBOARD}/Wh_Im", building_energy)
    for member, classes in BREAKDOWN.items():
        yield Binding(f"{DASHBOARD}/{member}", class_energy(classes))

    yield Binding(f"{DASHBOARD}/Total IT Load", _site("it_kw"))
    yield Binding(f"{DASHBOARD}/Total IT Load 2", _site("it_kw", 1e-3))
    yield Binding(f"{DASHBOARD}/Total Facility Load", _site("facility_kw"))
    yield Binding(f"{DASHBOARD}/Total Facility Load 2", _site("facility_kw", 1e-3))
    yield Binding(f"{DASHBOARD}/PUE", _ratio("facility_kw", "it_kw"))
    yield Binding(f"{DASHBOARD}/WUE", _ratio("makeup_lph", "it_kw"))
    yield Binding(
        f"{DASHBOARD}/Plant Efficiency", _ratio("cooling_elec_kw", "cooling_load_kw", KW_PER_RT)
    )
    yield Binding(
        f"{DASHBOARD}/Chilled-water Plant Efficiency",
        _ratio("chw_plant_kw", "chw_load_kw", KW_PER_RT),
    )
    for label, window in ROLLING.items():
        avg = f"avg.{window}."
        yield Binding(f"{DASHBOARD}/PUE ({label})", _ratio(f"{avg}facility_kw", f"{avg}it_kw"))
        if window != "year":
            yield Binding(f"{DASHBOARD}/WUE ({label})", _ratio(f"{avg}makeup_lph", f"{avg}it_kw"))
    yield Binding(
        f"{DASHBOARD}/Plant Efficiency (Daily)",
        _ratio("avg.day.cooling_elec_kw", "avg.day.cooling_load_kw", KW_PER_RT),
    )
    yield Binding(f"{DASHBOARD}/IT Power Chain Efficiency", _it_chain_efficiency)
    yield Binding(f"{DASHBOARD}/Transformer Efficiency", _transformer_efficiency)
    yield Binding(f"{DASHBOARD}/UPS Load Factor", _ratio("it_kw", "ups_capacity_kw", 100.0))


def _other(asset_model: AssetModel) -> Iterable[Binding]:
    schedule = asset_model.point(f"{OTHER}/Maintenance Schedule").export_value
    yield Binding(f"{OTHER}/IT Load", _site("it_kw"))
    yield Binding(f"{OTHER}/Cooling", _site("cooling_elec_kw"))
    yield Binding(f"{OTHER}/Lighting", _site("lighting_kw"))
    yield Binding(f"{OTHER}/Power Losses", _site("losses_kw"))
    yield Binding(f"{OTHER}/Maintenance Due", lambda s: s.time * 1000 >= schedule)


def _read(node: str, read: Callable[[dict[str, Scalar]], Scalar]) -> Callable[[WorldState], Scalar]:
    return lambda s: read(s.assets[node])


def _var(node: str, name: str, scale: float = 1.0) -> Callable[[WorldState], float]:
    return lambda s: s.assets[node][name] * scale


def _site(name: str, scale: float = 1.0) -> Callable[[WorldState], float]:
    return lambda s: s.assets[SITE][name] * scale


def _ratio(num: str, den: str, den_scale: float = 1.0) -> Callable[[WorldState], float]:
    """`num / (den / den_scale)`; a zero denominator makes the point Bad rather than a
    made-up number."""

    def read(s: WorldState) -> float:
        site = s.assets[SITE]
        denominator = site[den] / den_scale
        return site[num] / denominator if denominator else float("nan")

    return read


def _it_chain_efficiency(s: WorldState) -> float:
    """IT power as a share of IT power and the losses in the chain feeding it; Bad with no
    IT Load."""
    site = s.assets[SITE]
    total = site["it_kw"] + site["losses_kw"]
    return 100.0 * site["it_kw"] / total if total > 0.0 else float("nan")


def _transformer_efficiency(s: WorldState) -> float:
    """What the transformers deliver as a share of what the utility supplies them; Bad with
    no utility supply."""
    site = s.assets[SITE]
    utility = site["utility_kw"]
    return 100.0 * (1.0 - site["transformer_loss_kw"] / utility) if utility > 0.0 else float("nan")
