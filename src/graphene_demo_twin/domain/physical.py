from __future__ import annotations

from dataclasses import dataclass, field, replace
from math import ceil, sqrt
from typing import Any


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


@dataclass(frozen=True)
class AssetState:
    export_path: str
    type_id: str
    running: bool
    load_fraction: float = 0.0
    power_kw: float = 0.0
    flow_lps: float = 0.0
    metrics: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class NetworkBalance:
    zone_cooling_demand_kw: float
    pahu_cooling_demand_kw: float
    pahu_chw_flow_lps: float
    cooling_demand_kw: float
    cooling_delivered_kw: float
    unmet_cooling_kw: float
    condenser_rejection_kw: float
    cooling_plant_power_kw: float
    non_cooling_aux_kw: float


@dataclass(frozen=True)
class CoolingControlState:
    minimum_chillers: int
    maximum_chillers: int
    available_chillers: int
    required_chillers: int
    running_chillers: int
    chiller_load_limit_fraction: float
    chws_setpoint_c: float
    plant_load_fraction: float


@dataclass(frozen=True)
class ThermalZoneState:
    zone_id: str
    zone_heat_kw: float
    airside_cooling_kw: float
    unmet_cooling_kw: float
    temperature_c: float
    return_air_temperature_c: float
    humidity_pct: float


@dataclass(frozen=True)
class PhysicalWorld:
    assets: dict[str, AssetState]
    balance: NetworkBalance
    site: dict[str, Any]
    cooling_control: CoolingControlState
    thermal_zones: dict[str, ThermalZoneState] = field(default_factory=dict)

    def asset(self, export_path: str | None) -> AssetState | None:
        if not export_path:
            return None
        return self.assets.get(export_path.lower())


class PhysicalWorldSolver:
    """Deterministic asset-state and network-balance solver for the cooling plant."""

    def __init__(self, topology: dict, physics: dict):
        self.topology = topology
        self.physics = physics
        self.assets = topology.get("assets", [])
        self.by_id = {asset["assetId"]: asset for asset in self.assets}
        self.by_path = {asset["exportPath"].lower(): asset for asset in self.assets}
        self.by_type: dict[str, list[dict]] = {}
        for asset in self.assets:
            self.by_type.setdefault(asset.get("typeId") or "", []).append(asset)
        for rows in self.by_type.values():
            rows.sort(key=lambda row: row["exportPath"].lower())
        self.serves: dict[str, list[str]] = {}
        self.served_by: dict[str, list[str]] = {}
        for relation in topology.get("relations", []):
            if relation.get("kind") != "serves":
                continue
            src = self.by_id.get(relation.get("from"))
            dst = self.by_id.get(relation.get("to"))
            if not src or not dst:
                continue
            self.serves.setdefault(src["exportPath"].lower(), []).append(dst["exportPath"])
            self.served_by.setdefault(dst["exportPath"].lower(), []).append(src["exportPath"])

    @staticmethod
    def _constraint(constraints: dict[str, dict[str, float]], path: str, key: str, default: float) -> float:
        return float(constraints.get(path.lower(), {}).get(key, default))

    def solve(
        self,
        timestamp,
        site_seed: dict[str, Any],
        constraints: dict[str, dict[str, float]] | None = None,
    ) -> PhysicalWorld:
        constraints = {key.lower(): value for key, value in (constraints or {}).items()}
        states: dict[str, AssetState] = {}
        outside = float(site_seed["outsideTempC"])
        it_kw = float(site_seed["itLoadKw"])

        cooling_cfg = self.physics.get("cooling", {})
        airside_cfg = self.physics.get("airside", {})
        facility_cfg = self.physics.get("facility", {})
        environment_cfg = self.physics.get("environment", {})
        chw_delta_t_c = float(cooling_cfg.get("chwDesignDeltaTC", 5.5))
        chws_setpoint_c = float(cooling_cfg.get("chwsSetpointC", 7.0))
        minimum_chillers = max(0, int(cooling_cfg.get("minimumChillers", 1)))
        maximum_chillers = max(minimum_chillers, int(cooling_cfg.get("maximumChillers", 4)))
        chiller_load_limit = clamp(
            float(cooling_cfg.get("chillerLoadLimitFraction", 0.85)),
            0.05,
            1.0,
        )
        zone_cooling_demand_kw = max(
            0.0,
            it_kw * float(cooling_cfg.get("itHeatFraction", 0.92))
            + max(0.0, outside - 24.0) * float(cooling_cfg.get("envelopeKwPerC", 18.0)),
        )

        pahus = self.by_type.get("PAHU", [])
        pahu_cooling_demand_kw = zone_cooling_demand_kw
        pahu_chw_flow_lps = 0.0
        if pahus:
            pahu_cooling_demand_kw = 0.0
            per_unit_zone_demand_kw = zone_cooling_demand_kw / len(pahus)
            pahu_design_kw = float(airside_cfg.get("pahuDesignCoolingKw", 220.0))
            sat_setpoint_c = float(airside_cfg.get("pahuSupplyAirTempSetpointC", 15.0))
            rat_setpoint_c = float(airside_cfg.get("pahuReturnAirTempSetpointC", 25.0))
            supply_rh_setpoint = float(airside_cfg.get("pahuSupplyAirRhSetpointPct", 47.5))
            return_rh_setpoint = float(airside_cfg.get("pahuReturnAirRhSetpointPct", 52.5))
            static_pressure_setpoint = float(
                airside_cfg.get("pahuStaticPressureSetpointPa", 600.0)
            )
            design_static_pressure = max(
                1.0, float(airside_cfg.get("designStaticPressureSetpointPa", 450.0))
            )
            pressure_speed_factor = sqrt(
                max(0.1, static_pressure_setpoint / design_static_pressure)
            )
            after_hours_extra_fraction = max(
                0.0, float(airside_cfg.get("pahuAfterHoursExtraDemandFraction", 0.25))
            )
            return_rh = (
                float(site_seed.get("hallARhPct", 52.0))
                + float(site_seed.get("hallBRhPct", 52.0))
            ) / 2.0

            for asset in pahus:
                path = asset["exportPath"]
                upstream_chillers = [
                    upstream
                    for upstream in self.served_by.get(path.lower(), [])
                    if self.by_path.get(upstream.lower(), {}).get("typeId") == "Chiller"
                ]
                connected = bool(upstream_chillers)
                availability = clamp(
                    self._constraint(constraints, path, "availability", 1.0), 0.0, 1.0
                )
                after_hours = clamp(
                    self._constraint(constraints, path, "after_hours_operation", 0.0),
                    0.0,
                    1.0,
                )
                running = connected and availability > 0.05
                demand_multiplier = 1.0 + after_hours_extra_fraction * after_hours
                cooling_demand = (
                    per_unit_zone_demand_kw * demand_multiplier * availability
                    if running
                    else 0.0
                )
                load = clamp(cooling_demand / max(1.0, pahu_design_kw), 0.0, 1.0)
                fan_speed = (
                    clamp((0.42 + 0.38 * load) * pressure_speed_factor, 0.0, 1.0)
                    if running
                    else 0.0
                )
                if running and after_hours > 0.0:
                    fan_speed = max(
                        fan_speed,
                        clamp((0.58 + 0.12 * after_hours) * availability, 0.0, 1.0),
                    )
                valve = (
                    clamp(cooling_demand / max(1.0, pahu_design_kw), 0.0, 1.0)
                    if running
                    else 0.0
                )
                chw_flow = (
                    cooling_demand / max(1.0, 4.186 * max(0.1, chw_delta_t_c))
                    if running
                    else 0.0
                )
                static_pressure = (
                    static_pressure_setpoint * (0.97 + 0.03 * fan_speed)
                    if running
                    else 0.0
                )
                sat = (
                    sat_setpoint_c + 0.35 * (1.0 - load)
                    if running
                    else rat_setpoint_c
                )
                rat = rat_setpoint_c + 0.6 * load
                supply_rh = max(35.0, return_rh - 4.0 * valve)

                pahu_cooling_demand_kw += cooling_demand
                pahu_chw_flow_lps += chw_flow
                states[path.lower()] = AssetState(
                    path,
                    "PAHU",
                    running=running,
                    load_fraction=load,
                    flow_lps=chw_flow,
                    metrics={
                        "Cooling Demand": cooling_demand,
                        "Supply Air Temperature": sat,
                        "Return Air Temperature": rat,
                        "Supply Air Temperature Setpoint": sat_setpoint_c,
                        "Return Air Temperature Setpoint": rat_setpoint_c,
                        "Supply Air Relative Humidity": supply_rh,
                        "Return Air Relative Humidity": return_rh,
                        "Supply Air Relative Humidity Setpoint": supply_rh_setpoint,
                        "Return Air Relative Humidity Setpoint": return_rh_setpoint,
                        "Static Pressure": static_pressure,
                        "Static Pressure Setpoint": static_pressure_setpoint,
                        "Fan Speed Command": fan_speed * 100.0,
                        "Fan Speed Feedback": fan_speed * 100.0,
                        "CHW Valve Command": valve * 100.0,
                        "CHW Valve Feedback": valve * 100.0,
                        "CHW Flow": chw_flow,
                        "On_Off": 1 if running else 0,
                        "Fan On_Off": 1 if running else 0,
                        "Auto_Manual": 1,
                    },
                )

        cooling_demand_kw = pahu_cooling_demand_kw if pahus else zone_cooling_demand_kw

        cracs = self.by_type.get("CRAC", [])
        crac_fan_kw = 0.0
        crac_delivered_fraction = 1.0
        if cracs:
            delivered = 0.0
            per_unit_demand = cooling_demand_kw / len(cracs)
            crac_design_kw = float(airside_cfg.get("cracDesignCoolingKw", 110.0))
            for asset in cracs:
                path = asset["exportPath"]
                availability = clamp(self._constraint(constraints, path, "availability", 1.0), 0.0, 1.0)
                valve = clamp(self._constraint(constraints, path, "valve_position", 1.0), 0.0, 1.0)
                valve_command = clamp(self._constraint(constraints, path, "valve_command", 0.72), 0.0, 1.0)
                effective = availability * valve
                unit_delivered = per_unit_demand * effective
                delivered += unit_delivered
                fan_speed = 0.42 + 0.42 * clamp(per_unit_demand / crac_design_kw, 0.0, 1.0)
                fan_power = float(airside_cfg.get("cracFanDesignPowerKw", 4.5)) * fan_speed**3 * availability
                crac_fan_kw += fan_power
                chw_flow = unit_delivered / (4.186 * chw_delta_t_c) if unit_delivered > 0 else 0.0
                sat = 14.2 + (1.0 - effective) * 8.2
                rat = 24.0 + (1.0 - effective) * 1.8
                running = availability > 0.05
                states[path.lower()] = AssetState(
                    path,
                    "CRAC",
                    running=running,
                    load_fraction=clamp(per_unit_demand / crac_design_kw, 0.0, 1.0),
                    power_kw=fan_power,
                    flow_lps=chw_flow,
                    metrics={
                        "CHW Valve Command": valve_command * 100.0,
                        "CHW Valve Feedback": valve * 100.0,
                        "CHW Flow": chw_flow,
                        "CHW Supply Temperature": chws_setpoint_c,
                        "CHW Return Temperature": (
                            chws_setpoint_c + chw_delta_t_c if effective > 0 else chws_setpoint_c
                        ),
                        "Supply Air Temperature": sat,
                        "Return Air Temperature": rat,
                        "Fan Electrical Power": fan_power,
                        "On_Off": 1 if running else 0,
                        "Status": "RUNNING" if running else "STANDBY",
                    },
                )
            crac_delivered_fraction = clamp(delivered / max(1.0, cooling_demand_kw), 0.0, 1.0)

        chillers = self.by_type.get("Chiller", [])
        chiller_capacity_kw = float(cooling_cfg.get("chillerDesignCapacityKw", 1450.0))
        available_chillers = len(chillers)
        effective_maximum_chillers = min(available_chillers, maximum_chillers)
        effective_minimum_chillers = min(effective_maximum_chillers, minimum_chillers)
        staging_capacity_kw = max(1.0, chiller_capacity_kw * chiller_load_limit)
        required_chillers = (
            min(
                effective_maximum_chillers,
                max(effective_minimum_chillers, ceil(cooling_demand_kw / staging_capacity_kw)),
            )
            if effective_maximum_chillers
            else 0
        )
        configured_plant_capacity_kw = max(
            1.0,
            chiller_capacity_kw * chiller_load_limit * max(1, maximum_chillers),
        )
        plant_load_fraction = clamp(cooling_demand_kw / configured_plant_capacity_kw, 0.0, 1.0)
        active_chillers = chillers[:required_chillers]
        active_paths = {asset["exportPath"].lower() for asset in active_chillers}
        requested_per_chiller = cooling_demand_kw / max(1, required_chillers)
        required_chw_flow_lps = (
            requested_per_chiller / max(1.0, 4.186 * max(0.1, chw_delta_t_c))
            if required_chillers
            else 0.0
        )

        pump_power_kw = 0.0
        pump_flow_by_chiller: dict[str, float] = {}
        pump_links_by_chiller: set[str] = set()
        pump_design_flow_lps = float(cooling_cfg.get("chillerPumpDesignFlowLps", 90.0))
        pump_design_power_kw = float(cooling_cfg.get("chillerPumpDesignPowerKw", 32.0))
        pump_nominal_speed = clamp(float(cooling_cfg.get("chillerPumpNominalSpeedFraction", 0.74)), 0.0, 1.0)
        for pump in self.by_type.get("Chiller Pump", []):
            path = pump["exportPath"]
            downstream = [
                target
                for target in self.serves.get(path.lower(), [])
                if target.lower() in active_paths
            ]
            availability = clamp(self._constraint(constraints, path, "availability", 1.0), 0.0, 1.0)
            running = bool(downstream) and availability > 0.05
            speed = pump_nominal_speed * availability if running else 0.0
            flow = pump_design_flow_lps * speed
            power = pump_design_power_kw * speed**3
            pump_power_kw += power
            dp = 230.0 * speed**2
            states[path.lower()] = AssetState(
                path,
                "Chiller Pump",
                running=running,
                load_fraction=speed,
                power_kw=power,
                flow_lps=flow,
                metrics={
                    "Power": power,
                    "Current": power * 1000.0 / max(1.0, 400.0 * 1.732 * 0.9),
                    "Frequency": speed * 50.0,
                    "Speed Command": speed * 100.0,
                    "Speed Feedback": speed * 100.0,
                    "Flow": flow,
                    "Suction Pressure": 180.0,
                    "Discharge Pressure": 180.0 + dp,
                    "Differential Pressure": dp,
                    "On_Off": 1 if running else 0,
                    "Status": "RUNNING" if running else "STANDBY",
                },
            )
            if downstream:
                share = flow / len(downstream)
                for target in downstream:
                    key = target.lower()
                    pump_links_by_chiller.add(key)
                    pump_flow_by_chiller[key] = pump_flow_by_chiller.get(key, 0.0) + share

        towers = self.by_type.get("Cooling Tower", [])
        tower_conditions: dict[str, tuple[str, float, float, float]] = {}
        tower_power_kw = 0.0
        for tower in towers:
            path = tower["exportPath"]
            availability = clamp(self._constraint(constraints, path, "availability", 1.0), 0.0, 1.0)
            downstream = self.serves.get(path.lower(), [])
            active_downstream = [target for target in downstream if target.lower() in active_paths]
            running = bool(active_downstream) and availability > 0.05
            load = clamp(requested_per_chiller / chiller_capacity_kw, 0.0, 1.0) if running else 0.0
            fan_speed = (
                clamp(
                    (0.38 + 0.45 * load + 0.018 * max(0.0, outside - 27.0)) * availability,
                    0.0,
                    1.0,
                )
                if running
                else 0.0
            )
            power = float(cooling_cfg.get("towerFanDesignPowerKw", 26.0)) * fan_speed**3
            tower_power_kw += power
            wet_bulb = outside - 4.2
            approach = 3.0 + 5.5 * (1.0 - availability) + 1.4 * (1.0 - fan_speed if running else 1.0)
            cws = wet_bulb + approach
            flow = float(cooling_cfg.get("towerDesignFlowLps", 82.0)) * fan_speed if running else 0.0
            cwr = cws + (7.5 if running else 0.0)
            faulted = availability < 0.95
            states[path.lower()] = AssetState(
                path,
                "Cooling Tower",
                running=running,
                load_fraction=load,
                power_kw=power,
                flow_lps=flow,
                metrics={
                    "Power": power,
                    "Electrical Power": power,
                    "Frequency": fan_speed * 50.0,
                    "Current": power * 1000.0 / max(1.0, 400.0 * 1.732 * 0.92),
                    "Voltage": 400.0,
                    "PF": 0.92 if running else 0.0,
                    "Power Factor": 0.92 if running else 0.0,
                    "On_Off": 1 if running else 0,
                    "CWR Temperature": cwr,
                    "CWS Temperature": cws,
                    "Approach": approach,
                    "Fan Speed Command": fan_speed * 100.0,
                    "Fan Speed Feedback": fan_speed * 100.0,
                    "Basin Level": 72.0,
                    "Status": "RUNNING" if running else "STANDBY",
                    "Fault": faulted,
                    "System Failure_Trip": faulted,
                    "HasAlarm": faulted,
                    "General Alarm": faulted,
                    "Auto_Manual": 1,
                },
            )
            flow_share = flow / len(active_downstream) if active_downstream else 0.0
            for target in active_downstream:
                tower_conditions[target.lower()] = (path, availability, cws, flow_share)

        chiller_power_kw = 0.0
        condenser_rejection_kw = 0.0
        cooling_delivered_kw = 0.0
        tower_rejection_kw: dict[str, float] = {}
        for asset in chillers:
            path = asset["exportPath"]
            path_key = path.lower()
            is_selected = path_key in active_paths
            availability = clamp(self._constraint(constraints, path, "availability", 1.0), 0.0, 1.0)
            condenser_degradation = clamp(
                self._constraint(constraints, path, "condenser_degradation", 0.0),
                0.0,
                1.0,
            )
            tower_path, tower_avail, cws, cw_flow = tower_conditions.get(
                path_key,
                ("", 1.0, outside - 0.5, 80.0),
            )
            condenser_factor = clamp(tower_avail * (1.0 - 0.35 * condenser_degradation), 0.25, 1.0)
            hydraulic_factor = (
                clamp(pump_flow_by_chiller.get(path_key, 0.0) / max(1.0, required_chw_flow_lps), 0.0, 1.0)
                if path_key in pump_links_by_chiller
                else 1.0
            )
            running = is_selected and availability > 0.05 and hydraulic_factor > 0.01
            capacity = (
                chiller_capacity_kw
                * chiller_load_limit
                * availability
                * (0.58 + 0.42 * condenser_factor)
                * hydraulic_factor
            )
            cooling = min(requested_per_chiller, capacity) if running else 0.0
            cop = (
                clamp(
                    5.15
                    - 0.13 * max(0.0, cws - 27.0)
                    - 1.15 * condenser_degradation
                    - 1.25 * (1.0 - tower_avail),
                    2.35,
                    5.35,
                )
                if running
                else 0.0
            )
            power = cooling / cop if cop > 0 else 0.0
            rejection = cooling + power
            chiller_power_kw += power
            condenser_rejection_kw += rejection
            cooling_delivered_kw += cooling
            load = cooling / chiller_capacity_kw if chiller_capacity_kw else 0.0
            chw_supply = chws_setpoint_c + 1.8 * (1.0 - condenser_factor) if running else 11.0
            chw_return = chw_supply + (chw_delta_t_c if running else 0.0)
            chw_flow = cooling / max(1.0, 4.186 * max(0.1, chw_return - chw_supply)) if running else 0.0
            if path_key in pump_links_by_chiller:
                chw_flow = min(chw_flow, pump_flow_by_chiller.get(path_key, 0.0))
            cwr = (
                cws + rejection / max(1.0, cw_flow * 4.186)
                if running and cw_flow > 0
                else cws + (7.5 if running else 0.0)
            )
            cond_pressure = 720.0 + max(0.0, cws - 27.0) * 28.0 + condenser_degradation * 150.0
            states[path_key] = AssetState(
                path,
                "Chiller",
                running=running,
                load_fraction=load,
                power_kw=power,
                flow_lps=chw_flow,
                metrics={
                    "Input Power": power,
                    "Power": power,
                    "Load": load * 100.0,
                    "Cooling Output": cooling,
                    "Cooling Load": cooling,
                    "COP": cop,
                    "kW per RT": 3.517 / cop if cop else 0.0,
                    "CHW Supply Temperature": chw_supply,
                    "CHW Return Temperature": chw_return,
                    "CHW Flow": chw_flow,
                    "Flow Rate": chw_flow,
                    "CW Supply Temperature": cws,
                    "CW Return Temperature": cwr,
                    "CW Flow": cw_flow,
                    "CW Approach": cws - (outside - 4.2),
                    "Condenser Pressure": cond_pressure,
                    "On_Off": 1 if running else 0,
                    "Status": "RUNNING" if running else "STANDBY",
                    "Enabled": running,
                },
            )
            if tower_path:
                tower_key = tower_path.lower()
                tower_rejection_kw[tower_key] = tower_rejection_kw.get(tower_key, 0.0) + rejection

        for tower_key, rejection in tower_rejection_kw.items():
            tower_state = states[tower_key]
            metrics = dict(tower_state.metrics)
            cws = float(metrics["CWS Temperature"])
            cwr = (
                cws + rejection / max(1.0, tower_state.flow_lps * 4.186)
                if tower_state.flow_lps > 0
                else cws
            )
            metrics["CWR Temperature"] = cwr
            metrics["Heat Rejection"] = rejection
            states[tower_key] = replace(tower_state, metrics=metrics)

        cooling_delivered_kw *= crac_delivered_fraction
        unmet = max(0.0, cooling_demand_kw - cooling_delivered_kw)
        thermal_load_kw = zone_cooling_demand_kw
        airside_cooling_kw = min(thermal_load_kw, cooling_delivered_kw)
        thermal_unmet_kw = max(0.0, thermal_load_kw - airside_cooling_kw)
        thermal_unmet_fraction = thermal_unmet_kw / max(1.0, thermal_load_kw)
        thermal_gain_c = float(environment_cfg.get("thermalUnmetGainC", 4.5))
        return_air_delta_c = float(environment_cfg.get("returnAirDeltaC", 0.8))
        humidity_unmet_gain_pct = float(environment_cfg.get("humidityUnmetGainPct", 3.0))
        hall_a_temperature = (
            float(site_seed["hallATempC"]) + thermal_gain_c * thermal_unmet_fraction
        )
        hall_a_humidity = max(
            0.0,
            min(
                100.0,
                float(site_seed["hallARhPct"])
                + humidity_unmet_gain_pct * thermal_unmet_fraction,
            ),
        )
        thermal_zones = {
            "Hall-A": ThermalZoneState(
                zone_id="Hall-A",
                zone_heat_kw=thermal_load_kw,
                airside_cooling_kw=airside_cooling_kw,
                unmet_cooling_kw=thermal_unmet_kw,
                temperature_c=hall_a_temperature,
                return_air_temperature_c=hall_a_temperature + return_air_delta_c,
                humidity_pct=hall_a_humidity,
            )
        }
        legacy_hall_b_penalty = thermal_gain_c * unmet / max(1.0, cooling_demand_kw)
        site = dict(site_seed)
        site["coolingDemandKw"] = round(cooling_demand_kw, 3)
        site["coolingDeliveredKw"] = round(cooling_delivered_kw, 3)
        site["unmetCoolingKw"] = round(unmet, 3)
        site["hallATempC"] = round(hall_a_temperature, 3)
        site["hallARhPct"] = round(hall_a_humidity, 3)
        site["hallBTempC"] = round(
            float(site_seed["hallBTempC"]) + legacy_hall_b_penalty, 3
        )
        cooling_plant_power_kw = chiller_power_kw + tower_power_kw + pump_power_kw + crac_fan_kw
        non_cooling_aux_kw = float(facility_cfg.get("nonCoolingAuxKw", 180.0))
        site["plantLoadKw"] = round(cooling_plant_power_kw, 3)
        site["facilityLoadKw"] = round(it_kw + cooling_plant_power_kw + non_cooling_aux_kw, 3)
        site["pue"] = round(site["facilityLoadKw"] / max(1.0, it_kw), 3)
        balance = NetworkBalance(
            zone_cooling_demand_kw=zone_cooling_demand_kw,
            pahu_cooling_demand_kw=pahu_cooling_demand_kw,
            pahu_chw_flow_lps=pahu_chw_flow_lps,
            cooling_demand_kw=cooling_demand_kw,
            cooling_delivered_kw=cooling_delivered_kw,
            unmet_cooling_kw=unmet,
            condenser_rejection_kw=condenser_rejection_kw,
            cooling_plant_power_kw=cooling_plant_power_kw,
            non_cooling_aux_kw=non_cooling_aux_kw,
        )
        running_chillers = sum(
            1
            for state in states.values()
            if state.type_id == "Chiller" and state.running
        )
        cooling_control = CoolingControlState(
            minimum_chillers=minimum_chillers,
            maximum_chillers=maximum_chillers,
            available_chillers=available_chillers,
            required_chillers=required_chillers,
            running_chillers=running_chillers,
            chiller_load_limit_fraction=chiller_load_limit,
            chws_setpoint_c=chws_setpoint_c,
            plant_load_fraction=plant_load_fraction,
        )
        return PhysicalWorld(states, balance, site, cooling_control, thermal_zones)
