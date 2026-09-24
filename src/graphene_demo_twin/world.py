"""The world as currently modelled: its domains, in step order, and its projection."""

from graphene_demo_twin.asset_model import AssetModel
from graphene_demo_twin.faults import STANDARD_CATALOG, FaultCatalog, FaultDomain
from graphene_demo_twin.plant_design import PlantDesign
from graphene_demo_twin.projection import (
    Projector,
    airside_bindings,
    electrical_bindings,
    life_safety_bindings,
    network_bindings,
    network_quality,
    plant_bindings,
    site_bindings,
    thermal_bindings,
    water_bindings,
)
from graphene_demo_twin.sim import Domain, ThermalZoneDomain, ZoneSensorDomain
from graphene_demo_twin.sim.airside import ChilledWaterUnitDomain, CracDomain
from graphene_demo_twin.sim.electrical import ElectricalDomain
from graphene_demo_twin.sim.it_load import ITLoadDomain
from graphene_demo_twin.sim.life_safety import FireDomain, LiftDomain
from graphene_demo_twin.sim.network import NetworkDomain
from graphene_demo_twin.sim.plant import ChillerPlantDomain
from graphene_demo_twin.sim.site import SiteLoadDomain, SitePowerDomain
from graphene_demo_twin.sim.water import WaterDomain
from graphene_demo_twin.sim.weather import WeatherDomain


def default_domains(catalog: FaultCatalog = STANDARD_CATALOG) -> list[Domain]:
    """Faults first, so every model reads this step's fault levels; then the control network
    (which only decides what the supervisor sees), the
    weather, the fire panel and lifts (whose zone alarms stop the fresh-air handlers before
    they step), the IT Load that drives the site, the DX CRAC units, the chiller plant, the
    chilled-water units and Cooling Blocks on the water it supplied, the water network
    that makes up what the towers lose, the other loads, the
    electrical network that supplies all of them, the thermal zones every load dissipates its
    power into and every unit cools, the sensors that observe the halls, and last the site's
    power totals."""
    return [
        FaultDomain(catalog),
        NetworkDomain(),
        WeatherDomain(),
        FireDomain(),
        LiftDomain(),
        ITLoadDomain(),
        CracDomain(),
        ChillerPlantDomain(),
        ChilledWaterUnitDomain(),
        WaterDomain(),
        SiteLoadDomain(),
        ElectricalDomain(),
        ThermalZoneDomain(),
        ZoneSensorDomain(),
        SitePowerDomain(),
    ]


SETTLING_S = ElectricalDomain.settling_s + max(d.settling_s for d in default_domains())
"""Longest time the world takes to settle after a disturbance, such as a Clear. The UPS
batteries recharge for up to ElectricalDomain.settling_s after power returns, and their
charger losses keep heating the UPS rooms all that time. Only after that can the rooms
begin to settle, which takes up to the longest settling time of any domain."""


def default_projector(
    asset_model: AssetModel, design: PlantDesign, catalog: FaultCatalog = STANDARD_CATALOG
) -> Projector:
    return Projector(
        asset_model,
        [
            *airside_bindings(asset_model, design),
            *site_bindings(asset_model, design),
            *thermal_bindings(asset_model, design),
            *electrical_bindings(asset_model, design),
            *plant_bindings(asset_model, design),
            *network_bindings(asset_model, design),
            *water_bindings(asset_model, design),
            *life_safety_bindings(asset_model, design),
        ],
        network_quality(asset_model, design),
    )
