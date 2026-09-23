import math
import struct

import pytest

from graphene_demo_twin.asset_model import SourceClass
from graphene_demo_twin.plant_design import PLANT_VIEWS
from graphene_demo_twin.projection import (
    Binding,
    PointSource,
    Projector,
    Quality,
    QualityBinding,
    fallback_value,
)
from graphene_demo_twin.sim import Event, Simulation, WorldState
from graphene_demo_twin.world import default_domains, default_projector

START = 1_790_000_000
HOT_AISLE_DH03 = "Temperature and Humidity/Datahall 3/Sensor 17/Temp"
HOT_AISLE_DH01 = "Temperature and Humidity/Datahall 1/Sensor 1/Temp"
DASH_DH03_ENERGY = "Dashboard/Energy/Floors/Level 1/Data Halls/DH03/IT Energy"
CRAC3 = "CRAC/L1_CRAC3"


@pytest.fixture(scope="module")
def projector(asset_model, plant_design) -> Projector:
    return default_projector(asset_model, plant_design)


def _sim(plant_design, seed: int = 7) -> Simulation:
    return Simulation(plant_design, default_domains(), seed, START)


def _is_float32(x: float) -> bool:
    return struct.unpack("f", struct.pack("f", x))[0] == x


def test_every_point_gets_one_value_of_its_data_type(asset_model, plant_design, projector):
    sim = _sim(plant_design)
    sim.advance(30)
    projection = projector.project(sim.state)

    assert projection.time == sim.time
    assert projection.values.keys() == asset_model.points.keys()
    for path, value in projection.values.items():
        match asset_model.point(path).data_type:
            case "Float4":
                assert type(value) is float and _is_float32(value), path
            case "Float8":
                assert type(value) is float, path
            case "Int4":
                assert type(value) is int and -(2**31) <= value < 2**31, path
            case "Int8":
                assert type(value) is int and -(2**63) <= value < 2**63, path
            case "Boolean":
                assert type(value) is bool, path
            case "DateTime":
                assert type(value) is int, path  # epoch milliseconds, as the export writes it
            case "String" | "DataSet" | "Document":
                assert type(value) is str, path
    assert all(projection.quality(p) is Quality.GOOD for p in projection.values)


def test_fallback_is_deterministic_and_prefers_the_export_value(asset_model):
    point = asset_model.point
    assert fallback_value(point("BCPM/2L1/Rack ID")) == "Bender"
    assert fallback_value(point("CRAC/L1_CRAC3/Return Air Temperature")) == 20.0
    assert fallback_value(point("Chiller/R_C1/Auto_Manual")) == 1
    assert fallback_value(point("Lift Monitoring System/Lift 1/Moving Until")) == 1788322462657
    # No value in the export: the data type's zero.
    assert fallback_value(point("Dashboard/Total IT Load 2")) == 0.0
    assert fallback_value(point("Chiller/R_C1/General Alarm")) is False
    # A UDT parameter binding is not a value.
    assert fallback_value(point("IPS/Circuit 6/Outgoing No")) == 0
    # Documents are exposed as JSON text.
    assert '"Avg Vin"' in fallback_value(point("Device Card Abbreviation"))


def test_fallback_points_do_not_move_with_time_seed_or_faults(asset_model, plant_design, projector):
    a, b = _sim(plant_design, seed=1), _sim(plant_design, seed=2)
    a.advance(10)
    b.advance(500)
    pa, pb = projector.project(a.state), projector.project(b.state)
    fallback = projector.coverage.paths(PointSource.FALLBACK)
    assert fallback
    assert all(pa.values[p] == pb.values[p] for p in fallback)


def test_coverage_lists_every_point_once_with_its_source(asset_model, projector):
    report = projector.coverage
    assert report.entries.keys() == asset_model.points.keys()
    counts = report.counts()
    assert set(counts) == {"physics", "plant_view", "fallback"}
    assert sum(counts.values()) == len(asset_model.points) == 8741
    assert counts["physics"] > 0 and counts["plant_view"] > 0

    for path, entry in report.entries.items():
        in_view = any(path.startswith(f"{v}/") for v in PLANT_VIEWS)
        if entry.source is PointSource.PLANT_VIEW:
            assert in_view, path
        if entry.source is PointSource.PHYSICS:
            assert not in_view, path
        point = asset_model.point(path)
        allowed = point.support or point.source_class is SourceClass.STATIC_METADATA
        assert entry.debt == (entry.source is PointSource.FALLBACK and not allowed), path

    assert report.entries[HOT_AISLE_DH03].source is PointSource.PHYSICS
    assert report.entries[DASH_DH03_ENERGY].source is PointSource.PLANT_VIEW
    assert report.entries["BCPM/2L1/Rack ID"].source is PointSource.FALLBACK
    assert not report.entries["BCPM/2L1/Rack ID"].debt
    assert report.entries["Chiller/R_C1/Input Power"].debt
    assert report.debt() == sum(e.debt for e in report.entries.values()) > 0


def test_placeholder_points_follow_the_hall_state(asset_model, plant_design, projector):
    sim = _sim(plant_design)
    sim.advance(60)
    p = projector.project(sim.state)
    halls = {n: s for n, s in sim.state.assets.items() if n.startswith("DH")}

    assert p.values[HOT_AISLE_DH03] == pytest.approx(halls["DH03"]["temp_c"], rel=1e-6)
    assert p.values[DASH_DH03_ENERGY] == halls["DH03"]["it_energy_kwh"]
    assert p.values["Dashboard/Total IT Load"] == pytest.approx(
        sum(s["it_load_kw"] for s in halls.values())
    )
    assert p.values["Dashboard/Total IT Load 2"] == pytest.approx(
        p.values["Dashboard/Total IT Load"] / 1000, rel=1e-6
    )
    assert p.values["Dashboard/Energy/Floors/Level 1/Data Halls/IT Energy"] == pytest.approx(
        sum(halls[h]["it_energy_kwh"] for h in ("DH01", "DH02", "DH03", "DH04"))
    )
    assert p.values["Dashboard/Energy/Building/Total IT Energy"] == pytest.approx(
        sum(s["it_energy_kwh"] for s in halls.values())
    )
    bcpm = [f"BCPM/3L{i}/Active Power" for i in (1, 2, 3)]
    assert sum(p.values[b] for b in bcpm) == pytest.approx(halls["DH03"]["it_load_kw"], rel=1e-6)


def test_a_fault_moves_only_the_points_downstream_of_it(plant_design, projector):
    sim = _sim(plant_design)
    sim.advance(10)
    fork = sim.fork()
    fork.schedule(Event(fork.time, "fault.inject", CRAC3, {"fault": "crac.compressor_trip"}))
    sim.advance(1800)
    fork.advance(1800)
    base, faulted = projector.project(sim.state), projector.project(fork.state)

    assert faulted.values[HOT_AISLE_DH03] > base.values[HOT_AISLE_DH03] + 5.0
    assert faulted.values[HOT_AISLE_DH01] == base.values[HOT_AISLE_DH01]
    changed = {p for p in base.values if base.values[p] != faulted.values[p]}
    downstream = {
        p
        for p in projector.coverage.paths(PointSource.PHYSICS)
        if "Datahall 3" in p or p.startswith(f"{CRAC3}/")
    }
    assert HOT_AISLE_DH03 in changed and f"{CRAC3}/System Failure_Trip" in changed
    assert changed <= downstream


def test_non_finite_physics_values_are_bad_quality(asset_model, plant_design, projector):
    state = _sim(plant_design).state.copy()
    state.assets[HOT_AISLE_DH03.removesuffix("/Temp")]["temp_c"] = math.nan
    p = projector.project(state)
    assert p.quality(HOT_AISLE_DH03) is Quality.BAD
    assert p.values[HOT_AISLE_DH03] == 0.0
    assert p.quality(HOT_AISLE_DH01) is Quality.GOOD


def test_bindings_must_name_distinct_asset_model_points(asset_model):
    def read(state: WorldState) -> float:
        return 1.0

    with pytest.raises(ValueError, match="not in the Asset Model"):
        Projector(asset_model, [Binding("Chiller/R_C9/Input Power", read)])
    with pytest.raises(ValueError, match="not in the Asset Model"):
        Projector(asset_model, [], [QualityBinding(("Chiller/R_C9/Input Power",), read)])
    with pytest.raises(ValueError, match="bound twice"):
        Projector(
            asset_model,
            [Binding("Chiller/R_C1/Input Power", read), Binding("Chiller/R_C1/Input Power", read)],
        )


def test_projecting_a_step_is_cheap(plant_design, projector):
    import time

    state = _sim(plant_design).state
    t = time.perf_counter()
    for _ in range(20):
        projector.project(state)
    assert (time.perf_counter() - t) / 20 < 0.05
