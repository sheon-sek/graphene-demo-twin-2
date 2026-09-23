import pytest

from graphene_demo_twin.asset_model import AssetModel, load_asset_model
from graphene_demo_twin.plant_design import PlantDesign, load_plant_design


@pytest.fixture(scope="session")
def asset_model() -> AssetModel:
    return load_asset_model()


@pytest.fixture(scope="session")
def plant_design(asset_model: AssetModel) -> PlantDesign:
    return load_plant_design(asset_model)
