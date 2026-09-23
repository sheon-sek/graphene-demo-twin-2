"""The world as currently modelled: its domains, in step order, and its projection."""

from graphene_demo_twin.asset_model import AssetModel
from graphene_demo_twin.faults import STANDARD_CATALOG, FaultCatalog, FaultDomain, Mechanism
from graphene_demo_twin.plant_design import PlantDesign
from graphene_demo_twin.projection import (
    Projector,
    electrical_bindings,
    placeholder_bindings,
    placeholder_quality,
    plant_bindings,
    site_bindings,
    thermal_bindings,
)
from graphene_demo_twin.sim import (
    Domain,
    PlaceholderCracDomain,
    PlaceholderNetworkDomain,
    ThermalZoneDomain,
    ZoneSensorDomain,
)
from graphene_demo_twin.sim.electrical import ElectricalDomain
from graphene_demo_twin.sim.it_load import ITLoadDomain
from graphene_demo_twin.sim.plant import ChillerPlantDomain
from graphene_demo_twin.sim.site import SiteLoadDomain, SitePowerDomain
from graphene_demo_twin.sim.weather import WeatherDomain


def default_domains(catalog: FaultCatalog = STANDARD_CATALOG) -> list[Domain]:
    """Faults first, so every model reads this step's fault levels; then the network, the
    weather and IT Load that drive the site, the air suppliers, the chiller plant that cools
    them, the other loads, the
    electrical network that supplies all of them, the thermal zones every load dissipates its
    power into, the sensors that observe the halls, and last the site's power totals."""
    return [
        FaultDomain(catalog),
        PlaceholderNetworkDomain(catalog.asset_types(Mechanism.QUALITY)),
        WeatherDomain(),
        ITLoadDomain(),
        PlaceholderCracDomain(),
        ChillerPlantDomain(),
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
            *placeholder_bindings(asset_model, design),
            *site_bindings(asset_model, design),
            *thermal_bindings(asset_model, design),
            *electrical_bindings(asset_model, design),
            *plant_bindings(asset_model, design),
        ],
        placeholder_quality(asset_model, design, catalog.asset_types(Mechanism.QUALITY)),
    )
