import pytest

from graphene_demo_twin.asset_model import SUPPORT_SCOPES, SourceClass, load_asset_model

SWITCH = "Network Switches/MAIN CORE SWITCH A"


def test_lookup_point_by_export_path(asset_model):
    point = asset_model.point("Chiller/R_C1/Input Power")
    assert point.path == "Chiller/R_C1/Input Power"
    assert point.name == "Input Power"
    assert point.member == "Input Power"
    assert point.type_id == "Chiller"
    assert point.udt_instance == "Chiller/R_C1"
    assert point.asset == "Chiller/R_C1"
    assert point.eng_unit == "kW"


def test_unknown_paths_raise_key_error(asset_model):
    with pytest.raises(KeyError):
        asset_model.point("Chiller/R_C9/Input Power")
    with pytest.raises(KeyError):
        asset_model.asset("Chiller/R_C9")
    with pytest.raises(KeyError):
        asset_model.points_of("Chiller/R_C9")


def test_lookup_points_by_asset_includes_nested_udt_instances(asset_model):
    switch = asset_model.asset(SWITCH)
    assert switch.type_id == "Network Switch"
    assert switch.is_asset

    points = asset_model.points_of(SWITCH)
    assert len(points) == 11 + 48 * 9
    assert [p.path for p in points] == sorted(p.path for p in points)
    assert all(p.asset == SWITCH for p in points)

    port = asset_model.udt_instances[f"{SWITCH}/Ports/Port 07"]
    assert port.type_id == "Network Switch Port"
    assert port.asset == SWITCH
    assert not port.is_asset

    speed = asset_model.point(f"{SWITCH}/Ports/Port 07/Speed")
    assert speed.member == "Ports/Port 07/Speed"
    assert speed.udt_instance == f"{SWITCH}/Ports/Port 07"
    assert asset_model.asset_of(speed.path) is switch


def test_standalone_points_belong_to_no_asset(asset_model):
    point = asset_model.point("Chiller System Control/Chillers/CH-004/Status")
    assert point.asset is None
    assert point.udt_instance is None
    assert point.type_id is None
    assert asset_model.asset_of(point.path) is None


def test_udt_types_record_inheritance_and_members(asset_model):
    cdu = asset_model.udt_types["CDU"]
    assert cdu.parent == "Dashboard"
    assert set(asset_model.udt_types["Dashboard"].members) <= set(cdu.members)
    assert "Production/GPQM144 Pro" in asset_model.udt_types
    assert "Ports/Port 48/Admin Status" in asset_model.udt_types["Network Switch"].members


def test_registry_is_deterministic_and_ordered_by_export_path(asset_model):
    assert list(asset_model.points) == sorted(asset_model.points)
    assert list(asset_model.assets) == sorted(asset_model.assets)
    assert list(asset_model.udt_instances) == sorted(asset_model.udt_instances)
    again = load_asset_model()
    assert list(again.points.values()) == list(asset_model.points.values())
    assert list(again.udt_instances.values()) == list(asset_model.udt_instances.values())


# --- Support Assets ---------------------------------------------------------------------------

SUPPORT_POINTS = [
    "Smart Alarm Logic/Line/Breaker1",
    "Smart Alarm Logic/Breaker/HasAlarm",
    "Dashboard/1A/PUE",
    "Testing/Heartbeat",
    "Testing/DITest1/DI1",
    "Meter/decoder1/GEM630CTL_Hex",
    "Breaker/Breaker1",
    "Line/Line1",
    "Line/Backup/Line1",
    "Temperature_Controls/Reactor_Temp",
    "Level_Monitoring/Silo_Level",
    "Pressure System/Pipe1_Press",
    "MQTT Tags/PLC 1/Example Tag",
    "PredictionCache/Model3",
    "Device Card Abbreviation",
]

NOT_SUPPORT_POINTS = [
    "DemoRack/Breaker1/Trip",
    "Dashboard/PUE",
    "Dashboard/Energy/Building/Total Energy",
    "TIW/CDU-01/PUE",
    "Meter/Meter2/V1",
    "Meter/HasAlarm_Level 1",
    "Chiller/R_C1/Input Power",
]


@pytest.mark.parametrize("path", SUPPORT_POINTS)
def test_support_points_are_flagged(asset_model, path):
    point = asset_model.point(path)
    assert point.support
    assert point.source_class is SourceClass.SUPPORT


@pytest.mark.parametrize("path", NOT_SUPPORT_POINTS)
def test_physical_and_plant_view_points_are_not_support(asset_model, path):
    point = asset_model.point(path)
    assert not point.support
    assert point.source_class is not SourceClass.SUPPORT


def test_support_assets_are_flagged(asset_model):
    support = {a.path for a in asset_model.assets.values() if a.support}
    expected_prefixes = ("Smart Alarm Logic/", "Testing/", "Dashboard/")
    assert "Meter/decoder1" in support
    assert {f"Dashboard/{n}A" for n in range(1, 7)} <= support
    assert all(p == "Meter/decoder1" or p.startswith(expected_prefixes) for p in support)
    assert len(support) == 58 + 4 + 6 + 1
    # Support flags cover whole Assets, including every point beneath them.
    for asset_path in support:
        assert all(p.support for p in asset_model.points_of(asset_path))


def test_every_support_scope_matches_something(asset_model):
    for scope in SUPPORT_SCOPES:
        assert any(p == scope or p.startswith(f"{scope}/") for p in asset_model.points), scope


def test_support_flag_is_exactly_the_support_source_class(asset_model):
    for point in asset_model.points.values():
        assert point.support == (point.source_class is SourceClass.SUPPORT), point.path


# --- Source classes ---------------------------------------------------------------------------

CLASSIFIED = {
    SourceClass.COMMAND: [
        "Chiller System Control/Chillers/CH-001/Commands/Start",
        "Chiller System Control/Controls/DP PID/Setpoint (SP)",
        "Chiller System Control/Controls/DP PID/Control Variable",
        "Chiller System Control/Cooling Blocks/CB-001/CHWS Temperature SP",
        "Buffer Tank/R_BT1/Normally Opened Valve Open Command",
        "Buffer Tank/R_BT1/Recharge Valve Control",
        "CRAC/G_CRAC1/Supply Air Temperature Setpoint",
        "Cooling Towers Plant/R_P1_P1/VSD Speed Control",
        "Chiller System Control/Stage Up Wait Time",
        "Chiller System Control/Minimum DP",
    ],
    SourceClass.FEEDBACK: [
        "Chiller/R_C1/On_Off",
        "Chiller/R_CV1/On_Off",
        "DemoRack/Breaker1/OnOff",
        "Buffer Tank/R_BT1/Normally Opened Valve Open Status",
        "Buffer Tank/R_BT1/Recharge Valve Feedback",
        "CRAC/G_CRAC1/EC Fan Speed",
        "Chiller System Control/Pumps/P-CHWR-01/Speed",
        "Chiller_System/Bypass Valves/BV-001/Position",
        "Chiller System Control/Chillers/CH-001/Chiller On_Off Status",
    ],
    SourceClass.PROCESS_VALUE: [
        "Chiller/R_C1/Input Power",
        "Meter/Meter2/V1",
        "CRAC/G_CRAC1/Return Air Temperature",
        "Temperature and Humidity/Datahall 1/Sensor 1/Temp",
        "Chiller_System/Main Headers/TS-01/Temperature",
        "Chiller System Control/Weather/Wet Bulb Temperature",
        "UPS/UPS 1/Frequency",
        "Dashboard/PUE",
    ],
    SourceClass.EQUIPMENT_STATE: [
        "Chiller/R_C1/Auto_Manual",
        "Chiller System Control/Chillers/CH-001/Current State",
        "Chiller System Control/Chillers/CH-001/Status",
        "Chiller System Control/Running Chillers",
        "Lift Monitoring System/Lift 1/Door Status",
        "Buffer Tank/R_BT1/Chiller Buffer Tank Recharge",
    ],
    SourceClass.FAULT_ALARM: [
        "Chiller/R_C1/HasAlarm",
        "Chiller/R_C1/General Alarm",
        "Chiller/R_C1/System Failure_Trip",
        "DemoRack/Breaker1/Trip",
        "UPS/UPS 1/Rectifier Failure",
        "Buffer Tank/R_BT1/Normally Opened Valve Fail To Open",
        "Fire Protection System/Ground/Zone 1/SD1",
        "Chiller System Control/Alarms/Active Count",
        "Meter/HasAlarm_Level 1",
    ],
    SourceClass.ENERGY_INTEGRAL: [
        "Meter/Meter2/Wh_Im",
        "BCPM/1L1/Accumulated Energy",
        "Dashboard/Energy/Building/Total Energy",
        "Chiller/R_C1/Unit Operating Hours",
        "Chiller System Control/Chillers/CH-001/Run Hours",
    ],
    SourceClass.NETWORK_STATE: [
        f"{SWITCH}/Ports/Port 01/Link Status",
        f"{SWITCH}/Ports Up",
        f"{SWITCH}/Comm",
        "Other/Gateway 1 Status",
    ],
    SourceClass.STATIC_METADATA: [
        "Chiller System Control/Chillers/CH-001/Equipment Name",
        f"{SWITCH}/Ports/Port 01/Description",
        "BCPM/1L1/Rack ID",
        "Water Leak Detection System/Ground/1A/Cable Length",
    ],
}


@pytest.mark.parametrize(
    ("path", "expected"),
    [(path, cls) for cls, paths in CLASSIFIED.items() for path in paths],
)
def test_source_class(asset_model, path, expected):
    assert asset_model.point(path).source_class is expected


def test_every_source_class_is_used(asset_model):
    used = {p.source_class for p in asset_model.points.values()}
    assert used == set(SourceClass)
