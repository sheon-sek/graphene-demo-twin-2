"""Point bindings for the thermal zones and the sensors that observe them.

Each Temperature and Humidity sensor reports its hot-aisle reading and each Environment
Monitoring sensor its cold-aisle one, and `Environment Monitoring/<floor>/<hall>` aggregates
summarise that hall's cold-aisle sensors. `Chiller System Control/Data Halls` is a Plant View
of the zones themselves: what each hall's air is, not what any one sensor reads.
"""

import statistics
from collections.abc import Callable, Iterable

from graphene_demo_twin.asset_model import AssetModel
from graphene_demo_twin.plant_design import PlantDesign
from graphene_demo_twin.projection.projector import Binding
from graphene_demo_twin.sim import WorldState
from graphene_demo_twin.sim.thermal import cold_aisle_sensors, halls, hot_aisle_sensors

DATA_HALLS = "Chiller System Control/Data Halls"


def thermal_bindings(asset_model: AssetModel, design: PlantDesign) -> list[Binding]:
    bindings = [*_hot_aisle(design), *_cold_aisle(design), *_zones(design)]
    missing = [b.path for b in bindings if b.path not in asset_model.points]
    if missing:
        raise ValueError(f"thermal bindings name points not in the Asset Model: {missing}")
    return bindings


def _hot_aisle(design: PlantDesign) -> Iterable[Binding]:
    for sensor in hot_aisle_sensors(design):
        yield Binding(f"{sensor}/Temp", _var(sensor, "temp_c"))
        yield Binding(f"{sensor}/Humidity", _var(sensor, "rh_pct"))


def _cold_aisle(design: PlantDesign) -> Iterable[Binding]:
    """Each cold-aisle sensor's reading, and its hall's average and maximum over them."""
    by_hall: dict[str, list[str]] = {h: [] for h in halls(design)}
    for sensor, (hall, _) in cold_aisle_sensors(design).items():
        by_hall[hall].append(sensor)
        yield Binding(f"{sensor}/Temperature", _var(sensor, "temp_c"))
        yield Binding(f"{sensor}/Humidity", _var(sensor, "rh_pct"))
    for hall, sensors in by_hall.items():
        folder = f"Environment Monitoring/{design.room(hall).floor}/{hall}"
        for name, var in (("Temp", "temp_c"), ("Humidity", "rh_pct")):
            yield Binding(f"{folder}/Avg Cold Aisle {name}", _over(sensors, var, statistics.fmean))
            yield Binding(f"{folder}/Max Cold Aisle {name}", _over(sensors, var, max))


def _zones(design: PlantDesign) -> Iterable[Binding]:
    """Each hall's air temperature (`DH5` is DH05), and the heat all the halls put out."""
    for hall in halls(design):
        yield Binding(f"{DATA_HALLS}/DH{int(hall[2:])} Temperature", _var(hall, "temp_c"))
    yield Binding(f"{DATA_HALLS}/Current Heat Load", _over(list(halls(design)), "heat_kw", sum))


def _var(node: str, name: str) -> Callable[[WorldState], float]:
    return lambda s: s.assets[node][name]


def _over(nodes: list[str], name: str, reduce: Callable) -> Callable[[WorldState], float]:
    nodes = tuple(nodes)
    return lambda s: reduce([s.assets[n][name] for n in nodes])
