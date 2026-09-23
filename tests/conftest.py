import pytest

from graphene_demo_twin.asset_model import AssetModel, load_asset_model


@pytest.fixture(scope="session")
def asset_model() -> AssetModel:
    return load_asset_model()
