"""Point bindings for the chiller plant: its UDT equipment and both of its Plant Views.

`Chiller System Control` (the supervisory view) and `Chiller_System` (the instrument view)
observe the same plant (ADR-0004): every point of either reads the one AssetState that holds
its quantity, so a supervisory value, an instrument reading and a UDT member for the same
thing can never disagree. Flows the instrument view reports in m³/h are the same flows the
supervisory view reports in L/s; pressures in bar the same as in kPa.

The Cooling Blocks and Ceiling Cooling Units of `Chiller_System` belong to the airside (#22)
and stay unbound here.
"""

from collections.abc import Callable, Iterable

from graphene_demo_twin.asset_model import AssetModel
from graphene_demo_twin.plant_design import PlantDesign
from graphene_demo_twin.projection.projector import Binding, VariableRead
from graphene_demo_twin.sim import Scalar, WorldState
from graphene_demo_twin.sim.plant import (
    CELL_PF,
    CHILLER_KWR,
    CLOSING,
    LAYERS,
    OFF,
    PLANT,
    ROTATION_SCHEDULE,
    RUNNING,
    RUNON,
    Leg,
    hand,
    plant_layout,
    run_request,
)

CSC = "Chiller System Control"
CS = "Chiller_System"
M3H = 3.6
"""m³/h per L/s."""
BAR = 0.01
"""bar per kPa."""
STATIC_KPA = 200.0
"""Static pressure the pressurisation set holds the chilled-water loop at."""
CW_STATIC_KPA = 50.0
"""Static head on the condenser-water pump suction (the tower basins)."""
CW_HEAD_KPA = 180.0

type Read = Callable[[WorldState], Scalar]


def plant_bindings(asset_model: AssetModel, design: PlantDesign) -> list[Binding]:
    layout = plant_layout(design)
    bindings = [
        *_supervisor(layout),
        *_instruments(layout),
    ]
    for i, leg in enumerate(layout.legs, 1):
        bindings += _leg(leg, i)
    bindings += _pumps_and_valves(layout)
    missing = [b.path for b in bindings if b.path not in asset_model.points]
    if missing:
        raise ValueError(f"plant bindings name points not in the Asset Model: {missing}")
    return bindings


# ---- Readers


def _var(node: str, name: str, scale: float = 1.0) -> Read:
    if scale == 1.0:
        return VariableRead(node, name)
    return lambda s: s.assets[node][name] * scale


def _plant(name: str, scale: float = 1.0) -> Read:
    return _var(PLANT, name, scale)


def _const(value: Scalar) -> Read:
    return lambda s: value


def _fn(node: str, f: Callable[[dict[str, Scalar]], Scalar]) -> Read:
    return lambda s: f(s.assets[node])


def _on_off(c: dict[str, Scalar]) -> str:
    return "ON" if c["running"] else "OFF"


def _status(c: dict[str, Scalar]) -> str:
    """A chiller's status, as the supervisor shows it."""
    if c["trip"]:
        return "TRIPPED"
    if not c["enabled"]:
        return "DISABLED"
    if c["running"]:
        return "RUNNING"
    if c["seq"] in (RUNON, CLOSING):
        return "STOPPING"
    if c["seq"] != OFF:
        return "STARTING"
    return "STANDBY"


def _current_state(c: dict[str, Scalar]) -> str:
    status = _status(c)
    return f"{status} (HAND)" if hand(c) else status


def _alarm_status(c: dict[str, Scalar]) -> str:
    if c["trip"]:
        return f"SAFETY TRIP: {c['trip']}"
    if c["cycling"]:
        return "CYCLING SHUTDOWN"
    return "NO ALARMS"


def _tank_status(t: dict[str, Scalar]) -> str:
    if t["alarm"]:
        return "HIGH TEMPERATURE"
    return {0: "STANDBY", 1: "NORMAL", 2: "RECHARGING"}[t["mode"]]


def _level(t: dict[str, Scalar]) -> float:
    """A closed buffer tank runs full."""
    return 100.0


# ---- Chiller System Control


def _supervisor(layout) -> Iterable[Binding]:
    p = _plant
    legs = layout.legs

    def running(s: WorldState) -> int:
        return s.assets[PLANT]["running_count"]

    def loop_status(s: WorldState) -> str:
        plant = s.assets[PLANT]
        if plant["running_count"] == 0:
            return "NO COOLING"
        if plant["critical"] or plant["chws_c"] > plant["chws_sp_c"] + 1.0:
            return "DEGRADED"
        return "NORMAL"

    def tank_mode(s: WorldState) -> str:
        modes = {s.assets[t]["mode"] for leg in legs for t in leg.tanks}
        return "RECHARGE" if 2 in modes else "NORMAL"

    def plant_load(s: WorldState) -> float:
        plant = s.assets[PLANT]
        n = plant["running_count"]
        return 100.0 * plant["supply_kw"] / (n * CHILLER_KWR) if n else 0.0

    root = {
        "Average DP": p("dp_avg"),
        "Average Flow Rate": p("flow_avg"),
        "Buffer Tank Mode": tank_mode,
        "CHWS Temp": p("chws_c"),
        "CHWR Temp": p("chwr_c"),
        "CWS Temp": p("cws_c"),
        "CWR Temp": p("cwr_c"),
        "Chiller Loop Status": loop_status,
        "Coefficient of Performance": p("cop"),
        "Control Variable": _var(layout.secondary, "cv_pct"),
        "Differential Pressure": p("dp_kpa"),
        "Differential Pressure Mode": p("dp_mode"),
        "Flow Rate": p("flow_lps"),
        "Flow Rate Mode": p("dp_mode"),
        "Maximum Chillers": p("max_chillers"),
        "Minimum Chillers": p("min_chillers"),
        "Minimum DP": p("min_dp_kpa"),
        "Minimum Flow Rate": _const(0.1 * 420.0),
        "Next To Start": p("next_start"),
        "Next To Stop": p("next_stop"),
        "Operating Mode": lambda s: "AUTO" if s.assets[PLANT]["dp_mode"] == "AUTO" else "MANUAL",
        "Plant Load": plant_load,
        "Process Variable (PV)": p("dp_kpa"),
        "Required Chillers": p("required"),
        "Running Available": lambda s: (
            f"{s.assets[PLANT]['running_count']}/{s.assets[PLANT]['available_count']}"
        ),
        "Running Chillers": running,
        "Setpoint (SP)": p("dp_sp_kpa"),
        "Stage Down Wait Time": p("stage_down_wait_s"),
        "Stage Up Inhibit Wait Time": p("stage_up_inhibit_s"),
        "Stage Up Wait Time": p("stage_up_wait_s"),
        "System Normal": lambda s: loop_status(s) == "NORMAL" and not s.assets[PLANT]["alarms"],
        "Total Cooling Load (Demand)": p("demand_kw"),
        "Total Cooling Load (Supply)": p("supply_kw"),
        "Total Electrical Power Input": p("plant_kw"),
        # Alarm summary from the devices' own alarm logic.
        "Alarms/Active Count": p("alarms"),
        "Alarms/Critical Count": p("critical"),
        "Alarms/Warning Count": lambda s: s.assets[PLANT]["alarms"] - s.assets[PLANT]["critical"],
        "Alarms/Unacknowledged Count": p("alarms"),
        "Alarms/Latest Message": lambda s: s.assets[PLANT]["latest_alarm"] or "✓  NO ACTIVE ALARMS",
        # Controllers.
        "Controls/DP PID Mode": p("dp_mode"),
        "Controls/DP PID/Mode": p("dp_mode"),
        "Controls/DP PID/Control Variable": _var(layout.secondary, "cv_pct"),
        "Controls/DP PID/Manual Output": p("dp_manual_pct"),
        "Controls/DP PID/Process Variable (PV)": p("dp_kpa"),
        "Controls/DP PID/Setpoint (SP)": p("dp_sp_kpa"),
        "Controls/Bypass PID Mode": p("bp_mode"),
        "Controls/Bypass PID/Mode": p("bp_mode"),
        "Controls/Bypass PID/Control Variable": p("bp_cv_pct"),
        "Controls/Bypass PID/Manual Output": p("bp_manual_pct"),
        "Controls/Bypass PID/Process Variable (PV)": p("bp_kpa"),
        "Controls/Bypass PID/Setpoint (SP)": p("bp_sp_kpa"),
        "Controls/Demo Cooling Demand": lambda s: (
            100.0 * s.assets[PLANT]["demand_kw"] / (len(legs) * CHILLER_KWR)
        ),
        "Controls/Last Command": p("last_command"),
        "Controls/Maximum Chillers": p("max_chillers"),
        "Controls/Minimum Chillers": p("min_chillers"),
        "Controls/Selected Lead": p("lead"),
        "Controls/Staging Command Pending": p("pending"),
        "Controls/Staging Strategy": _const("BY RUNNING HOURS"),
        # Plant setpoints, held for the Cooling Blocks.
        "Cooling Blocks/CB-001/CHW Differential Pressure SP": p("dp_sp_kpa", BAR),
        "Cooling Blocks/CB-001/CHWS Temperature SP": p("chws_sp_c"),
        "Cooling Blocks/CB-001/CWS Temperature SP": p("cws_sp_c"),
        "Cooling Blocks/CB-001/Chiller Load Limit": p("load_limit_pct"),
        "Cooling Blocks/CB-001/Pump Minimum Speed": p("pump_min_pct"),
        "Cooling Blocks/CB-001/Tower Approach SP": p("approach_sp_k"),
        "Cooling Blocks/CB-001/System Status": loop_status,
        "Cooling Blocks/CB-001/Enabled": lambda s: s.assets[PLANT]["running_count"] > 0,
        "Cooling Blocks/CB-001/Loop Enable": lambda s: (
            "ENABLED" if s.assets[PLANT]["running_count"] > 0 else "DISABLED"
        ),
        "Cooling Blocks/CB-001/Mode": lambda s: (
            "AUTO" if s.assets[PLANT]["dp_mode"] == "AUTO" else "MANUAL"
        ),
        # Pumps.
        "Pumps/P-CHWR-01/Flow": p("flow_lps", M3H),
        "Pumps/P-CHWR-01/Frequency": _var(layout.secondary, "hz"),
        "Pumps/P-CHWR-01/Speed": _var(layout.secondary, "speed_pct"),
        "Pumps/P-CHWR-01/Status": _fn(
            layout.secondary, lambda x: "RUNNING" if x["running"] else "STOPPED"
        ),
        "Pumps/P-CHWR-01/Suction P": lambda s: (STATIC_KPA - s.assets[PLANT]["dp_kpa"]) * BAR,
        "Pumps/P-CWS-01/Flow": p("cw_lps", M3H),
        "Pumps/P-CWS-01/Frequency": lambda s: _mean_running_hz(s, [leg.cw_pump for leg in legs]),
        "Pumps/P-CWS-01/Speed": lambda s: 2.0 * _mean_running_hz(s, [leg.cw_pump for leg in legs]),
        "Pumps/P-CWS-01/Status": lambda s: (
            "RUNNING" if any(s.assets[leg.cw_pump]["running"] for leg in legs) else "STOPPED"
        ),
        "Pumps/P-CWS-01/Discharge P": lambda s: (
            (CW_STATIC_KPA + (CW_HEAD_KPA if s.assets[PLANT]["cw_lps"] > 0.0 else 0.0)) * BAR
        ),
        # Loop instruments.
        "Sensors/Flow/CHWS FM": p("flow_lps", M3H),
        "Sensors/Flow/CHWR FM": p("flow_lps", M3H),
        "Sensors/Flow/CWS FM": p("cw_lps", M3H),
        "Sensors/Flow/CWR FM": p("cw_lps", M3H),
        "Sensors/Flow/Status": _const("NORMAL"),
        "Sensors/Pressure/CHWS PS": lambda s: STATIC_KPA * BAR,
        "Sensors/Pressure/CHWR PS": lambda s: (STATIC_KPA - s.assets[PLANT]["dp_kpa"]) * BAR,
        "Sensors/Pressure/CWS PS": lambda s: (
            (CW_STATIC_KPA + (CW_HEAD_KPA if s.assets[PLANT]["cw_lps"] > 0.0 else 0.0)) * BAR
        ),
        "Sensors/Pressure/CWR PS": _const(CW_STATIC_KPA * BAR),
        "Sensors/Pressure/Status": _const("NORMAL"),
        "Sensors/Temperature/CHWS TS": p("chws_c"),
        "Sensors/Temperature/CHWR TS": p("chwr_c"),
        "Sensors/Temperature/CWS TS": p("cws_c"),
        "Sensors/Temperature/CWR TS": p("cwr_c"),
        "Sensors/Temperature/Status": _const("NORMAL"),
        # Rotation schedule.
        "Rotation Schedule/Current Lead": p("lead"),
        "Rotation Schedule/Last Rotation": p("last_rotation"),
        "Rotation Schedule/Last Scheduled Key": p("last_key"),
        "Rotation Schedule/Monitor Status": p("monitor"),
        "Rotation Schedule/Rotation Count": p("rotations"),
    }
    for day, (enabled, _) in ROTATION_SCHEDULE.items():
        root[f"Rotation Schedule/{day}/Enabled"] = _const(enabled)
    for member, read in root.items():
        yield Binding(f"{CSC}/{member}", read)


def _run_request(c: dict[str, Scalar]) -> bool:
    """The run request the chiller's sequence follows: the sequencer's in auto, the
    selector's in hand."""
    return run_request(c)


def _mean_running_hz(s: WorldState, pumps: list[str]) -> float:
    running = [s.assets[p]["hz"] for p in pumps if s.assets[p]["running"]]
    return sum(running) / len(running) if running else 0.0


def _leg(leg: Leg, i: int) -> Iterable[Binding]:
    """One chiller and what serves it, in both Plant Views and on its UDTs."""
    c, name = leg.chiller, leg.name
    csc, cs = f"{CSC}/Chillers/{name}", f"{CS}/Chillers/{name}"
    run_h = _fn(c, lambda x: x["run_h0"] + x["run_s"] / 3600.0)
    mode = _fn(c, lambda x: "HAND" if hand(x) else "AUTO")
    supervisor = {
        "Alarm Status": _fn(c, _alarm_status),
        "Buffer Tank Status": lambda s: _tank_summary(s, leg),
        "CHWS Temp": _var(c, "chws_read_c"),
        "CHWR Temp": _var(c, "chwr_c"),
        "Chilled Water Supply Temp": _var(c, "chws_read_c"),
        "Chilled Water Return Temp": _var(c, "chwr_c"),
        "COP": _var(c, "cop"),
        "Chilled Water Flow Rate": _var(c, "chw_lps"),
        "Flow Rate": _var(c, "chw_lps"),
        "Chiller On_Off Status": _fn(c, _on_off),
        "Commands/Requested Mode": mode,
        "Commands/Reset": _var(c, "reset"),
        "Commands/Start": _fn(c, _run_request),
        "Commands/Stop": _fn(c, lambda x: not _run_request(x)),
        "Cooling Load": _var(c, "cooling_kw"),
        "Current State": _fn(c, _current_state),
        "Electrical Power": _var(c, "power_kw"),
        "Power": _var(c, "power_kw"),
        "Enabled": _var(c, "enabled"),
        "Load": _var(c, "load_pct"),
        "Mode": mode,
        "Ready To Start": _fn(
            c, lambda x: x["enabled"] and not hand(x) and not x["trip"] and x["seq"] == OFF
        ),
        "Ready To Stop": _fn(c, lambda x: x["seq"] == RUNNING and x["seq_s"] >= 300),
        "Run Hours": run_h,
        "Sequence State": _var(c, "seq"),
        "Status": _fn(c, _status),
    }
    for member, read in supervisor.items():
        yield Binding(f"{csc}/{member}", read)
    instruments = {
        f"CHWS-00{i}/Frequency": _var(leg.chw_pump, "hz"),
        f"CHWR-00{i}/Frequency": _var(leg.cw_pump, "hz"),
        "FM-01/Flow Rate": _var(c, "chw_lps", M3H),
        "FM-02/Flow Rate": _var(c, "cw_lps", M3H),
        "MV-01/Position": _var(leg.evap_valve, "pos_pct"),
        "MV-02/Position": _var(leg.cond_valve, "pos_pct"),
        "TS-01/Temperature": _var(c, "chws_read_c"),
        "TS-02/Temperature": _var(c, "chwr_c"),
        "TS-03/Temperature": _var(c, "cw_in_c"),
        "TS-04/Temperature": _var(c, "cw_out_c"),
        f"WCC-00{i}/Cooling Load": _var(c, "cooling_kw"),
        f"WCC-00{i}/Electrical Power": _var(c, "power_kw"),
        f"WCC-00{i}/Load": _var(c, "load_pct"),
    }
    for member, read in instruments.items():
        yield Binding(f"{cs}/{member}", read)
    if not c.startswith("~"):
        udt = {
            "Auto_Manual": _fn(c, lambda x: 0 if hand(x) else 1),
            "Compressor Motor Current": _var(c, "current_pct"),
            "Condenser - High Pressure": _var(c, "cond_hp_kpa"),
            "Condenser Pressure": _var(c, "cond_kpa"),
            "Discharge - High Temperature": _var(c, "disch_hi_c"),
            "Discharge - Low Temperature": _var(c, "disch_lo_c"),
            "Evaporator - Low Pressure": _var(c, "evap_kpa"),
            "Evaporator Small Temperature Difference": _var(c, "approach_c"),
            "General Alarm": _fn(c, lambda x: bool(x["trip"]) or x["cycling"]),
            "HasAlarm": _fn(c, lambda x: bool(x["trip"]) or x["cycling"]),
            "Input Power": _var(c, "power_kw"),
            "On_Off": _fn(c, lambda x: int(x["running"])),
            "System Failure_Trip": _fn(c, lambda x: bool(x["trip"])),
            "System Start Times": _var(c, "starts_day"),
            "Unit Cycling Fault Code": _var(c, "cycling"),
            "Unit Operating Hours": run_h,
            "Unit Safety Fault Code": _fn(c, lambda x: bool(x["trip"])),
        }
        for member, read in udt.items():
            yield Binding(f"{c}/{member}", read)

    # Its Tower Group, as the supervisor sees it, and each cell.
    g = leg.tower
    ct = f"{CSC}/Cooling Towers/{g}"

    def group_status(s: WorldState) -> str:
        cells = [s.assets[x] for x in leg.cells]
        if any(x["trip"] for x in cells):
            return "FAULT"
        return "RUNNING" if any(x["running"] for x in cells) else "STANDBY"

    tower = {
        "Alarm Status": lambda s: (
            "FAN TRIP" if any(s.assets[x]["trip"] for x in leg.cells) else "NO ALARMS"
        ),
        "Basin Level": _plant(f"{g}.level_pct"),
        "CWS Temp": _var(c, "cw_in_c"),
        "CWR Temp": _var(c, "cw_out_c"),
        "Fan Speed": _plant(f"{g}.fan_pct"),
        "Load": lambda s: 100.0 * s.assets[PLANT][f"{g}.rejected_kw"] / (1000.0 * len(leg.cells)),
        "Mode": _const("AUTO"),
        "Enabled": _var(c, "enabled"),
        "Power": _plant(f"{g}.power_kw"),
        "Run Hours": lambda s: (
            s.assets[PLANT][f"{g}.run_h0"] + s.assets[PLANT][f"{g}.run_s"] / 3600.0
        ),
        "Status": group_status,
    }
    for member, read in tower.items():
        yield Binding(f"{ct}/{member}", read)
    for cell in leg.cells:
        yield from _cell(cell)

    # Its buffer tanks.
    for tank in leg.tanks:
        yield from _tank(tank)


def _cell(cell: str) -> Iterable[Binding]:
    def amps(x: dict[str, Scalar]) -> float:
        volts = x["volts"]
        return 1000.0 * x["power_kw"] / (3**0.5 * volts * CELL_PF) if volts else 0.0

    members = {
        "Auto_Manual": _const(1),
        "Current": _fn(cell, amps),
        "Energy": _var(cell, "energy_kwh"),
        "Frequency": _var(cell, "fan_pct", 0.5),
        "General Alarm": _var(cell, "trip"),
        "HasAlarm": _var(cell, "trip"),
        "On_Off": _fn(cell, lambda x: int(x["running"])),
        "Power": _var(cell, "power_kw"),
        "Power Factor": _fn(cell, lambda x: CELL_PF if x["running"] else 0.0),
        "System Failure_Trip": _var(cell, "trip"),
        "Voltage": _var(cell, "volts"),
    }
    for member, read in members.items():
        yield Binding(f"{cell}/{member}", read)


def _tank(tank: str) -> Iterable[Binding]:
    tag = f"BT-00{tank.rsplit('BT', 1)[1]}"
    view = f"{CSC}/Buffer Tanks/{tag}"
    members: dict[str, Read] = {
        f"Buffer Tank Stratified Temperature {k}": _var(tank, f"t{k}") for k in range(1, LAYERS + 1)
    }
    members |= {
        "Buffer Tank Temperature Alarm": _var(tank, "alarm"),
        "Chilled Water Supply Inlet Temp": _var(tank, "in_c"),
        "Chilled Water Supply Outlet Temp": _var(tank, "t1"),
        "Chiller Buffer Tank Mode Status": _var(tank, "mode"),
        "Chiller Buffer Tank Recharge": _fn(tank, lambda x: x["recharge_lps"] > 0.0),
        "Chiller Buffer Tank Recharge Pipe Flow Meter": _var(tank, "recharge_lps"),
        "Recharge Valve Control": _var(tank, "rc_cmd_pct"),
        "Recharge Valve Feedback": _var(tank, "rc_pos_pct"),
        # The tank's inlet isolation (normally open) and bypass (normally closed) valves hold
        # their normal positions while the tank is in service.
        "Normally Opened Valve Auto_Manual Mode": _const(1),
        "Normally Opened Valve Open Command": _const(True),
        "Normally Opened Valve Close Command": _const(False),
        "Normally Opened Valve Open Status": _const(1),
        "Normally Opened Valve Close Status": _const(0),
        "Normally Opened Valve Fail To Open": _const(False),
        "Normally Closed Valve Auto_Manual Mode": _const(1),
        "Normally Closed Valve Open Command": _const(False),
        "Normally Closed Valve Close Command": _const(True),
        "Normally Closed Valve Open Status": _const(0),
        "Normally Closed Valve Close Status": _const(1),
        "Normally Closed Valve Fail To Close": _const(False),
    }
    for member, read in members.items():
        yield Binding(f"{tank}/{member}", read)
    supervisor = {
        "Alarm Status": _fn(tank, lambda x: "HIGH TEMPERATURE" if x["alarm"] else "NO ALARMS"),
        "Average Temp": _var(tank, "avg_c"),
        "Capacity": _fn(tank, _capacity),
        "Enabled": _const(True),
        "Flow Rate": _var(tank, "flow_lps"),
        "Inlet Temp": _var(tank, "in_c"),
        "Level": _fn(tank, _level),
        "Mode": _const("AUTO"),
        "Outlet Temp": _var(tank, "t1"),
        "Pressure": _var(tank, "pressure_kpa"),
        "Status": _fn(tank, _tank_status),
        "Water Temp": _var(tank, "avg_c"),
    }
    for member, read in supervisor.items():
        yield Binding(f"{view}/{member}", read)


def _capacity(t: dict[str, Scalar]) -> float:
    """Cold stored, as a share of a tank charged at 14 °C against 20 °C return water."""
    return min(max(100.0 * (20.0 - t["avg_c"]) / 6.0, 0.0), 100.0)


def _tank_summary(s: WorldState, leg: Leg) -> str:
    statuses = [_tank_status(s.assets[t]) for t in leg.tanks]
    for status in ("HIGH TEMPERATURE", "RECHARGING", "NORMAL"):
        if status in statuses:
            return status
    return "STANDBY"


# ---- Chiller_System headers and valves


def _instruments(layout) -> Iterable[Binding]:
    ms = f"{CS}/Main Headers"
    yield Binding(f"{ms}/TS-01/Temperature", _plant("chws_c"))
    yield Binding(f"{ms}/TS-02/Temperature", _plant("chwr_c"))
    yield Binding(f"{ms}/TS-03/Temperature", _plant("cws_c"))
    yield Binding(f"{ms}/TS-04/Temperature", _plant("cwr_c"))
    yield Binding(f"{ms}/TS-05/Temperature", _plant("t_ret"))
    yield Binding(f"{ms}/FM-01/Flow Rate", _plant("flow_lps", M3H))
    yield Binding(f"{ms}/DPS-01/Differential Pressure", _plant("dp_kpa"))
    yield Binding(f"{ms}/DPS-02/Differential Pressure", _plant("bp_kpa"))
    for i, leg in enumerate(layout.legs, 1):
        yield Binding(
            f"{CS}/Header Motorized Valves/MV-00{i}/Position", _var(leg.header_valve, "pos_pct")
        )
    for i, valve in enumerate(layout.bypass, 1):
        yield Binding(f"{CS}/Bypass Valves/BV-00{i}/Position", _var(valve, "pos_pct"))


def _pumps_and_valves(layout) -> Iterable[Binding]:
    chillers = [leg.chiller for leg in layout.legs if not leg.chiller.startswith("~")]
    yield Binding(
        "Chiller/HasAlarm_R",
        lambda s: any(s.assets[c]["trip"] or s.assets[c]["cycling"] for c in chillers),
    )
    for plant in ("P1", "P2"):
        cells = [c for leg in layout.legs for c in leg.cells if f"/R_{plant}_CT" in c]
        yield Binding(
            f"Cooling Towers Plant/HasAlarm_R_{plant}",
            lambda s, cells=cells: any(
                s.assets[c]["trip"] or s.assets[c.replace("_CT", "_P")]["has_alarm"] for c in cells
            ),
        )
    for pump in layout.pumps:
        yield Binding(f"{pump}/On_Off", _fn(pump, lambda x: int(x["running"])))
        yield Binding(f"{pump}/Power", _var(pump, "power_kw"))
    for valve in layout.valves:
        yield Binding(f"{valve}/On_Off", _fn(valve, lambda x: int(x["pos_pct"] > 1.0)))
