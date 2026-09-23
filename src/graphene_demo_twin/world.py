"""The world as currently modelled: its domains, in step order, and its projection."""

from graphene_demo_twin.asset_model import AssetModel
from graphene_demo_twin.faults import STANDARD_CATALOG, FaultCatalog, FaultDomain, Mechanism
from graphene_demo_twin.plant_design import PlantDesign
from graphene_demo_twin.projection import Projector, placeholder_bindings, placeholder_quality
from graphene_demo_twin.sim import (
    Domain,
    PlaceholderCracDomain,
    PlaceholderHallDomain,
    PlaceholderNetworkDomain,
    PlaceholderSensorDomain,
)


def default_domains(catalog: FaultCatalog = STANDARD_CATALOG) -> list[Domain]:
    """Faults first, so every model reads this step's fault levels; then the network, the
    air suppliers, the halls they cool and the sensors that observe the halls."""
    return [
        FaultDomain(catalog),
        PlaceholderNetworkDomain(catalog.asset_types(Mechanism.QUALITY)),
        PlaceholderCracDomain(),
        PlaceholderHallDomain(),
        PlaceholderSensorDomain(),
    ]


SETTLING_S = max(d.settling_s for d in default_domains())
"""Longest time any domain takes to settle after a disturbance, such as a Clear."""


def default_projector(
    asset_model: AssetModel, design: PlantDesign, catalog: FaultCatalog = STANDARD_CATALOG
) -> Projector:
    return Projector(
        asset_model,
        placeholder_bindings(asset_model, design),
        placeholder_quality(asset_model, design, catalog.asset_types(Mechanism.QUALITY)),
    )
