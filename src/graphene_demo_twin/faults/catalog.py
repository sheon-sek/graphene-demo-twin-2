"""The fault catalog: named failure mechanisms bound to an asset type, and their parameters."""

import math
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from graphene_demo_twin.plant_design import ConnectionKind, PlantDesign
from graphene_demo_twin.sim import Event, EventError
from graphene_demo_twin.sim.electrical import METER_TYPES, incomers
from graphene_demo_twin.sim.network import NTP, network, role_of
from graphene_demo_twin.sim.plant import plant_layout
from graphene_demo_twin.sim.water import water_layout

INJECT = "fault.inject"
CLEAR = "fault.clear"
MAX_MINUTES = 24 * 60
"""Longest ramp or auto-clear duration, in minutes."""


class FaultError(EventError):
    """A fault action cannot be taken: unknown fault, wrong asset, bad parameters."""


class FaultConflict(FaultError):
    """The fault is already active on that asset (inject), or not active (clear)."""


class FaultCategory(StrEnum):
    EQUIPMENT = "equipment"
    SENSOR = "sensor"
    COMMUNICATION = "communication"
    CONTROL = "control"
    EXTERNAL = "external"


class Mechanism(StrEnum):
    """How a fault reaches the world. None of them writes an alarm point: alarm bits come
    from device logic reading the state these variables change."""

    PHYSICAL_CONSTRAINT = "physical_constraint"
    """A physical quantity the simulation consumes (availability, degradation, derate)."""
    OBSERVATION = "observation"
    """A sensor's reading is corrupted; the physical world is unchanged."""
    QUALITY = "quality"
    """Communication degrades, so the points behind it lose quality."""
    CONTROLLER = "controller"
    """A Controller misbehaves (wrong setpoint, bad tuning) while the equipment is healthy."""

    @property
    def prefix(self) -> str:
        """Namespace of the AssetState variables this mechanism drives."""
        return _PREFIX[self]

    @property
    def spreads_along(self) -> frozenset[ConnectionKind]:
        """The Plant Design connection kinds its effect travels downstream along: the causal
        path. A quality loss follows the control network only; a corrupted reading goes
        nowhere, since the physical world is unchanged."""
        return _SPREADS[self]


_PREFIX = {
    Mechanism.PHYSICAL_CONSTRAINT: "constraint",
    Mechanism.OBSERVATION: "observation",
    Mechanism.QUALITY: "quality",
    Mechanism.CONTROLLER: "controller",
}

_PHYSICAL = frozenset(ConnectionKind) - {ConnectionKind.NET}
_SPREADS = {
    Mechanism.PHYSICAL_CONSTRAINT: _PHYSICAL,
    Mechanism.OBSERVATION: frozenset[ConnectionKind](),
    Mechanism.QUALITY: frozenset({ConnectionKind.NET}),
    Mechanism.CONTROLLER: _PHYSICAL,
}

MECHANISM_OF: Mapping[FaultCategory, Mechanism] = {
    FaultCategory.EQUIPMENT: Mechanism.PHYSICAL_CONSTRAINT,
    FaultCategory.EXTERNAL: Mechanism.PHYSICAL_CONSTRAINT,
    FaultCategory.SENSOR: Mechanism.OBSERVATION,
    FaultCategory.COMMUNICATION: Mechanism.QUALITY,
    FaultCategory.CONTROL: Mechanism.CONTROLLER,
}


@dataclass(frozen=True, slots=True)
class FaultSpec:
    """One catalog entry."""

    id: str
    name: str
    asset_type: str
    """UDT typeId of the assets this fault can be injected on."""
    category: FaultCategory
    variable: str
    """The AssetState variable the fault drives on its asset, `<mechanism prefix>.<name>`.
    Its value is the fault's current level times `span`; absent means no fault."""
    span: float
    """The variable's value at severity 1, in `unit`."""
    unit: str
    description: str
    default_severity: float = 1.0
    where: Callable[[PlantDesign], Iterable[str]] | None = None
    """The only assets of its type the fault can act on (a utility loss needs an incomer);
    None for every placed asset of the type."""

    @property
    def mechanism(self) -> Mechanism:
        return MECHANISM_OF[self.category]

    def targets(self, design: PlantDesign) -> tuple[str, ...]:
        """The placed assets this fault can be injected on."""
        if self.where is not None:
            return tuple(self.where(design))
        return tuple(
            a.path
            for a in design.assets.values()
            if a.type_id == self.asset_type and a.room and not a.support
        )


@dataclass(frozen=True, slots=True)
class FaultParams:
    """How hard, how fast and how long a fault acts."""

    severity: float = 1.0
    """Level reached after onset, in (0, 1]."""
    ramp_s: int = 0
    """Onset: 0 is a step, otherwise a linear ramp to `severity` over this many seconds."""
    auto_clear_s: int | None = None
    """Duration: None acts until cleared, otherwise the fault clears itself after this long."""

    _KEYS = frozenset({"fault", "severity", "ramp_min", "auto_clear_min"})

    @classmethod
    def parse(cls, params: Mapping[str, Any]) -> "FaultParams":
        """Parameters from event params `{severity?, ramp_min?, auto_clear_min?}`; raises
        FaultError on anything out of range or unknown."""
        if unknown := sorted(params.keys() - cls._KEYS):
            raise FaultError(f"unknown fault parameters: {unknown}")
        severity = _number(params.get("severity", 1.0), "severity")
        if not 0.0 < severity <= 1.0:
            raise FaultError(f"severity must be in (0, 1], got {severity}")
        ramp = _minutes(params.get("ramp_min", 0), "ramp_min", allow_zero=True)
        clear = params.get("auto_clear_min")
        auto_clear = None if clear is None else _minutes(clear, "auto_clear_min")
        if auto_clear is not None and ramp > auto_clear:
            raise FaultError("the ramp cannot outlast the auto-clear duration")
        return cls(float(severity), ramp, auto_clear)

    def as_params(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "ramp_min": self.ramp_s / 60,
            "auto_clear_min": None if self.auto_clear_s is None else self.auto_clear_s / 60,
        }


def _number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise FaultError(f"{name} must be a finite number, got {value!r}")
    return float(value)


def _minutes(value: Any, name: str, *, allow_zero: bool = False) -> int:
    minutes = _number(value, name)
    if minutes < 0 or (minutes == 0 and not allow_zero) or minutes > MAX_MINUTES:
        raise FaultError(f"{name} must be in {'[' if allow_zero else '('}0, {MAX_MINUTES}]")
    return round(minutes * 60)


def fault_key(target: str, fault: str) -> str:
    """Identifies one active fault: a fault is active at most once per asset."""
    return f"{fault}@{target}"


class FaultCatalog:
    """The faults that can be injected, by id and by asset type."""

    def __init__(self, specs: Iterable[FaultSpec]) -> None:
        self._specs: dict[str, FaultSpec] = {}
        for spec in specs:
            if spec.id in self._specs:
                raise ValueError(f"fault {spec.id} is catalogued twice")
            prefix = spec.mechanism.prefix
            if not spec.variable.startswith(f"{prefix}."):
                raise ValueError(
                    f"{spec.id} is a {spec.category} fault, so it acts through {spec.mechanism} "
                    f"and must drive a `{prefix}.` variable, not {spec.variable}"
                )
            self._specs[spec.id] = spec

    def __iter__(self) -> Iterator[FaultSpec]:
        return iter(self._specs.values())

    def __len__(self) -> int:
        return len(self._specs)

    def __contains__(self, fault_id: object) -> bool:
        return fault_id in self._specs

    def get(self, fault_id: str) -> FaultSpec:
        try:
            return self._specs[fault_id]
        except (KeyError, TypeError):
            raise FaultError(f"unknown fault: {fault_id!r}") from None

    def for_type(self, type_id: str) -> tuple[FaultSpec, ...]:
        return tuple(s for s in self if s.asset_type == type_id)

    def asset_types(self, mechanism: Mechanism | None = None) -> frozenset[str]:
        return frozenset(s.asset_type for s in self if mechanism in (None, s.mechanism))

    def check(self, event: Event, design: PlantDesign) -> str | None:
        """Why `event` is not a valid fault action on the asset it names, or None if it is.

        A fault targets exactly the asset in `event.target`: it must be a placed asset of
        the fault's type, never a room, a Support Asset or some other asset of that type.
        """
        if event.kind not in (INJECT, CLEAR):
            return f"not a fault action: {event.kind}"
        try:
            spec = self.get(event.params.get("fault"))
            if event.kind == INJECT:
                FaultParams.parse(event.params)
        except FaultError as e:
            return str(e)
        placed = design.assets.get(event.target)
        if placed is None or placed.support:
            return f"{event.target} is not an asset in the Plant Design"
        if placed.type_id != spec.asset_type:
            return (
                f"{spec.id} applies to {spec.asset_type} assets, "
                f"and {event.target} is a {placed.type_id}"
            )
        if spec.where is not None and event.target not in spec.targets(design):
            return f"{spec.id} applies only to {', '.join(spec.targets(design))}"
        return None


def _network_role(design: PlantDesign, role: str) -> tuple[str, ...]:
    return tuple(d for d in network(design).devices if role_of(d) == role)


def _sensor_faults(prefix: str, asset_type: str, aisle: str) -> tuple[FaultSpec, ...]:
    """The observation faults of a hall temperature sensor: the air it hangs in is unchanged,
    so its reading disagrees with the zone and with the sensors around it."""
    return (
        FaultSpec(
            f"{prefix}.offset",
            "Sensor offset",
            asset_type,
            FaultCategory.SENSOR,
            "observation.offset_c",
            5.0,
            "°C",
            f"The temperature element reads high by a fixed amount (a bad calibration or a "
            f"loose termination); the {aisle} air itself is unchanged.",
            default_severity=0.6,
        ),
        FaultSpec(
            f"{prefix}.drift",
            "Sensor drift",
            asset_type,
            FaultCategory.SENSOR,
            "observation.drift_c_per_h",
            4.0,
            "°C per hour",
            f"The temperature element drifts further high the longer the fault acts, until it "
            f"saturates; Clear recalibrates it. The {aisle} air itself is unchanged.",
            default_severity=0.5,
        ),
        FaultSpec(
            f"{prefix}.stuck",
            "Stuck reading",
            asset_type,
            FaultCategory.SENSOR,
            "observation.stuck",
            1.0,
            "stuck",
            "The sensor freezes on its last temperature and humidity readings (once the level "
            "passes half).",
        ),
    )


_CHW_UNITS = {"PAHU": "pahu", "FCU": "fcu", "FWU": "fwu", "Ceiling Cooling Units": "ccu"}
"""The air units on chilled water, and the prefix of their fault ids."""


def _secondary_pump(design: PlantDesign) -> tuple[str, ...]:
    return (plant_layout(design).secondary,)


def _life_safety_faults() -> tuple[FaultSpec, ...]:
    """Fire protection and the lifts (#26)."""
    detectors = {"smoke_detector": "Smoke Detector", "heat_detector": "Heat Detector"}
    return (
        FaultSpec(
            "fire.room_fire",
            "Fire",
            "Fire Zone",
            FaultCategory.EXTERNAL,
            "constraint.fire_kw",
            250.0,
            "kW",
            "A fire breaks out in the zone's room: smoke fills it and the ceiling heats, its "
            "detectors alarm, the zone's fresh-air handlers shut down and the lifts return to "
            "the ground floor. In a sprinklered zone the heads open, the alarm valve flows, the "
            "fire pumps start and the fire is knocked down. The fire's heat and the lost fresh "
            "air warm and humidify the room.",
        ),
        *(
            FaultSpec(
                f"{prefix}.fault",
                "Detector fault",
                kind,
                FaultCategory.EQUIPMENT,
                "constraint.detector_fault",
                1.0,
                "fault",
                "The detector fails (a dirty chamber, a broken head): the panel shows it in "
                "fault, and it no longer detects a fire in its zone.",
            )
            for prefix, kind in detectors.items()
        ),
        *(
            FaultSpec(
                f"{prefix}.false_alarm",
                "False alarm",
                kind,
                FaultCategory.SENSOR,
                "observation.false_alarm",
                1.0,
                "alarm",
                "The device reports a fire that is not there (dust or steam in a detector, a "
                "call point knocked): the zone goes into alarm, its fresh-air handlers shut "
                "down and the lifts are recalled, while the air itself is clean.",
            )
            for prefix, kind in {**detectors, "call_point": "Manual Call Point"}.items()
        ),
        FaultSpec(
            "lift.stuck",
            "Lift stuck",
            "Lift",
            FaultCategory.EQUIPMENT,
            "constraint.stuck",
            1.0,
            "stuck",
            "The car stops where it is, between floors if it was travelling, with its doors "
            "shut; on Clear it carries on to the landing it was heading for.",
        ),
    )


def _cooling_faults() -> tuple[FaultSpec, ...]:
    """The chiller plant's faults, and those of the air units on its chilled water (#23)."""
    return (
        FaultSpec(
            "chiller.trip",
            "Chiller trip",
            "Chiller",
            FaultCategory.EQUIPMENT,
            "constraint.trip",
            1.0,
            "trip",
            "A compressor fault trips the chiller on its safety chain (once the level passes "
            "half). The trip latches until Reset or 15 minutes after Clear; the sequencer starts "
            "the next chiller at once, and with none left the chilled water warms.",
        ),
        FaultSpec(
            "chiller.compressor_degradation",
            "Compressor degradation",
            "Chiller",
            FaultCategory.EQUIPMENT,
            "constraint.compressor_degradation",
            1.0,
            "fraction worn",
            "Worn impeller and leaking guide vanes: the compressor loses up to half its "
            "capacity and a third of its efficiency, so it draws more power per kW of cooling "
            "and, loaded up, cannot hold the chilled-water setpoint.",
            default_severity=0.6,
        ),
        FaultSpec(
            "chiller.condenser_fouling",
            "Condenser fouling",
            "Chiller",
            FaultCategory.EQUIPMENT,
            "constraint.condenser_fouling",
            1.0,
            "fraction fouled",
            "Scale on the condenser tubes widens the condensing approach: condenser pressure, "
            "discharge temperature and power rise at the same load, and past 1,200 kPa the "
            "chiller unloads.",
            default_severity=0.5,
        ),
        FaultSpec(
            "chiller.chws_sensor_drift",
            "CHW sensor drift",
            "Chiller",
            FaultCategory.SENSOR,
            "observation.drift_c_per_h",
            4.0,
            "°C per hour",
            "The chiller's leaving chilled-water sensor drifts high the longer the fault acts, "
            "until it saturates; Clear recalibrates it. The water itself, the header sensors "
            "and the units downstream are unchanged, so the reading disagrees with them.",
            default_severity=0.5,
        ),
        FaultSpec(
            "chiller.hand_mode",
            "Left in hand mode",
            "Chiller",
            FaultCategory.CONTROL,
            "controller.hand_mode",
            1.0,
            "hand",
            "The chiller's selector is left in hand and on (once the level passes half): it "
            "runs whatever the demand, outside the sequencer, which stops an auto chiller to "
            "make room. Surplus primary flow goes round the bypass and the plant draws more "
            "power. The equipment is healthy and raises no alarm.",
        ),
        FaultSpec(
            "tower.fan_failure",
            "Tower fan failure",
            "Cooling Tower",
            FaultCategory.EQUIPMENT,
            "constraint.fan_loss",
            1.0,
            "fraction of airflow",
            "The cell's fan loses airflow (a slipping belt, then a failed gearbox) and trips "
            "past 90 %. The other cells in its Tower Group speed up; if they cannot make up "
            "for it, condenser water warms, and with it condenser pressure and chiller power.",
        ),
        FaultSpec(
            "tower.group_fan_failure",
            "Tower Group fan failure",
            "Cooling Tower",
            FaultCategory.EQUIPMENT,
            "constraint.group_fan_loss",
            1.0,
            "fraction of airflow",
            "The starter panel feeding the fans of the cell's whole Tower Group fails: every "
            "cell loses airflow and trips past 90 %. Condenser water warms, then condenser "
            "pressure and chiller power, until the chiller's high-pressure trip hands its "
            "load to another; the sequencer starts no chiller on the group while it acts.",
        ),
        FaultSpec(
            "tower.fill_fouling",
            "Fill fouling",
            "Cooling Tower",
            FaultCategory.EQUIPMENT,
            "constraint.fill_fouling",
            1.0,
            "fraction fouled",
            "Scale and biofilm on the fill halve its heat transfer at full severity: the cell "
            "rejects less heat, so its group's fans run faster to hold condenser water.",
            default_severity=0.6,
        ),
        FaultSpec(
            "pump.trip",
            "Pump trip",
            "Chiller Pump",
            FaultCategory.EQUIPMENT,
            "constraint.trip",
            1.0,
            "trip",
            "The pump's motor overload trips (once the level passes half). A leg pump takes "
            "its chiller out of service and the sequencer starts the next; the secondary pump "
            "stops the chilled water to every air unit.",
        ),
        FaultSpec(
            "pump.bearing_wear",
            "Bearing wear",
            "Chiller Pump",
            FaultCategory.EQUIPMENT,
            "constraint.bearing_wear",
            1.0,
            "fraction worn",
            "Worn bearings add friction: the pump draws up to 30 % more power for the same "
            "speed and flow.",
            default_severity=0.6,
        ),
        FaultSpec(
            "pump.dp_pid_oscillation",
            "DP PID oscillation",
            "Chiller Pump",
            FaultCategory.CONTROL,
            "controller.gain_factor",
            4.0,
            "gain increase",
            "The DP PID driving the secondary pump is mistuned to up to five times its gains: "
            "the pump speed hunts and the header differential pressure swings round its "
            "setpoint, while the equipment is healthy and raises no alarm.",
            where=_secondary_pump,
        ),
        FaultSpec(
            "valve.stuck",
            "Valve stuck",
            "Chiller Valve",
            FaultCategory.EQUIPMENT,
            "constraint.stuck",
            1.0,
            "stuck",
            "The actuator seizes (once the level passes half) and the valve stays at the % "
            "open it was: its position no longer follows its command. A leg valve stuck "
            "short of open takes its chiller out of service; a stuck bypass valve leaves the "
            "others to carry the bypass PID.",
        ),
        FaultSpec(
            "buffer_tank.inlet_valve_stuck",
            "Inlet valve stuck shut",
            "Buffer Tank",
            FaultCategory.EQUIPMENT,
            "constraint.inlet_stuck",
            1.0,
            "stuck",
            "The tank's normally open inlet valve seizes shut (once the level passes half): it "
            "reports Fail To Open, and the tank Controller opens the bypass valve so its leg "
            "keeps its flow. The tank is out of service: it stops buffering the leg and its "
            "stored charge stands.",
        ),
        FaultSpec(
            "buffer_tank.bypass_valve_stuck",
            "Bypass valve stuck open",
            "Buffer Tank",
            FaultCategory.EQUIPMENT,
            "constraint.bypass_stuck",
            1.0,
            "stuck",
            "The tank's normally closed bypass valve seizes open (once the level passes half): "
            "it reports Fail To Close, and half the leg's flow goes round the tank, mixing "
            "unbuffered chiller water into the supply.",
        ),
        FaultSpec(
            "cdu.pump_failure",
            "CDU pump failure",
            "CDU",
            FaultCategory.EQUIPMENT,
            "constraint.trip",
            1.0,
            "fail",
            "The CDU's pump set fails (once the level passes half): it stops carrying DH08's "
            "liquid-cooled share of IT Load to the chilled water, and that heat ends up in "
            "the hall air.",
        ),
        *(
            spec
            for type_id, prefix in _CHW_UNITS.items()
            for spec in (
                FaultSpec(
                    f"{prefix}.fan_failure",
                    "Fan failure",
                    type_id,
                    FaultCategory.EQUIPMENT,
                    "constraint.fan_loss",
                    1.0,
                    "fraction of airflow",
                    "The supply fan loses airflow: the unit delivers less cooling to its "
                    "zone, and below half airflow its differential-pressure switch alarms.",
                ),
                FaultSpec(
                    f"{prefix}.filter_choke",
                    "Filter choke",
                    type_id,
                    FaultCategory.EQUIPMENT,
                    "constraint.filter_blockage",
                    0.6,
                    "fraction of airflow",
                    "Air filters clog and throttle airflow; the filter switch alarms past "
                    "25 % blockage.",
                    default_severity=0.6,
                ),
            )
        ),
    )


STANDARD_CATALOG = FaultCatalog(
    [
        FaultSpec(
            "crac.fan_failure",
            "Fan failure",
            "CRAC",
            FaultCategory.EQUIPMENT,
            "constraint.fan_loss",
            1.0,
            "fraction of airflow",
            "EC fan motor degrades and loses airflow; below 10 % airflow the unit trips on its "
            "airflow switch.",
        ),
        FaultSpec(
            "crac.filter_choke",
            "Filter choke",
            "CRAC",
            FaultCategory.EQUIPMENT,
            "constraint.filter_blockage",
            0.6,
            "fraction of airflow",
            "Air filters clog and throttle airflow; the filter differential-pressure switch "
            "alarms past 25 % blockage.",
            default_severity=0.6,
        ),
        FaultSpec(
            "crac.compressor_trip",
            "Compressor trip",
            "CRAC",
            FaultCategory.EQUIPMENT,
            "constraint.compressor_trip",
            1.0,
            "trip",
            "The high-pressure switch trips the compressor circuit and the unit shuts down "
            "(trips once the level passes half).",
        ),
        FaultSpec(
            "crac.high_ambient",
            "High condenser ambient",
            "CRAC",
            FaultCategory.EXTERNAL,
            "constraint.condenser_derate",
            0.7,
            "fraction of capacity",
            "Hot-air recirculation at the outdoor condenser derates the DX circuit; head "
            "pressure alarms past 40 % derate.",
            default_severity=0.8,
        ),
        FaultSpec(
            "crac.setpoint_drift",
            "Setpoint drift",
            "CRAC",
            FaultCategory.CONTROL,
            "controller.setpoint_offset_c",
            8.0,
            "°C",
            "The unit controller's supply-air setpoint drifts upwards, so it unloads the "
            "compressor while the equipment is healthy and raises no alarm.",
        ),
        FaultSpec(
            "crac.compressor_failure",
            "Compressor failure",
            "CRAC",
            FaultCategory.EQUIPMENT,
            "constraint.compressor_loss",
            1.0,
            "loss",
            "The lead compressor's circuit locks out on its high-pressure switch (once the "
            "level passes half): the unit keeps running on its lag compressor, which carries "
            "under a third of its capacity, so the hall warms.",
        ),
        FaultSpec(
            "crac.comm_loss",
            "Communication loss",
            "CRAC",
            FaultCategory.COMMUNICATION,
            "quality.comm_loss",
            1.0,
            "fraction of polls lost",
            "The unit stops answering BMS polls: its points go uncertain, then bad at half "
            "severity, and the supervisor raises Loss of Signal.",
        ),
        *_sensor_faults("th", "Temperature and Humidity", "hot-aisle"),
        *_sensor_faults("em", "Environment Monitoring", "cold-aisle"),
        FaultSpec(
            "th.comm_loss",
            "Communication loss",
            "Temperature and Humidity",
            FaultCategory.COMMUNICATION,
            "quality.comm_loss",
            1.0,
            "fraction of polls lost",
            "The sensor stops answering polls: uncertain, then bad at half severity.",
        ),
        FaultSpec(
            "network.device_down",
            "Device down",
            "Network Device",
            FaultCategory.COMMUNICATION,
            "quality.comm_loss",
            1.0,
            "fraction of packets lost",
            "The device stops forwarding: it and everything the supervisor reaches only "
            "through it lose quality.",
        ),
        FaultSpec(
            "network.switch_failure",
            "Switch failure",
            "Network Device",
            FaultCategory.COMMUNICATION,
            "quality.device_failure",
            1.0,
            "fail",
            "The switch dies (once the level passes half): its links go down, and every "
            "device and field point the supervisor reaches only through it goes Bad and "
            "stale. The equipment behind it keeps running. On Clear it reboots.",
            where=lambda design: _network_role(design, "switch"),
        ),
        FaultSpec(
            "network.gateway_failure",
            "Gateway failure",
            "Network Device",
            FaultCategory.COMMUNICATION,
            "quality.device_failure",
            1.0,
            "fail",
            "The field gateway dies (once the level passes half): every point of the systems "
            "it carries goes Bad and stale, while the equipment keeps running. On Clear it "
            "reboots.",
            where=lambda design: _network_role(design, "gateway"),
        ),
        FaultSpec(
            "network.port_flap",
            "Port flap",
            "Network Switch",
            FaultCategory.COMMUNICATION,
            "quality.port_flap",
            1.0,
            "fraction of each cycle down",
            "The switch's first uplink port flaps: its link drops for part of every 20 s, "
            "errors climb, the switch works harder, and everything reached through the link "
            "turns Uncertain.",
            default_severity=0.5,
        ),
        FaultSpec(
            "network.server_overload",
            "Server overload",
            "Network Device",
            FaultCategory.EQUIPMENT,
            "constraint.cpu_overload",
            1.0,
            "share of spare CPU consumed",
            "A runaway process eats the server's spare CPU and memory: it answers slowly and "
            "warns. A SCADA server past 95 % CPU overruns its scan, so what only it serves "
            "goes Uncertain; its redundant partner keeps the rest good.",
            where=lambda design: _network_role(design, "server"),
        ),
        FaultSpec(
            "network.ntp_drift",
            "NTP drift",
            "Network Device",
            FaultCategory.SENSOR,
            "observation.clock_drift_s_per_h",
            60.0,
            "s/h",
            "The time server's clock drifts, growing until Clear resynchronises it; it warns "
            "once it is a second out.",
            where=lambda design: (NTP,) if NTP in design.assets else (),
        ),
        FaultSpec(
            "weather.high_wet_bulb",
            "High wet bulb",
            "Weather Station",
            FaultCategory.EXTERNAL,
            "constraint.wet_bulb_rise_c",
            4.0,
            "°C",
            "A humid spell raises the outdoor wet bulb above the season's: the towers reject "
            "heat less readily, the chillers lift harder and PUE rises.",
            default_severity=0.75,
        ),
        FaultSpec(
            "it.load_surge",
            "IT load surge",
            "IT Load",
            FaultCategory.EXTERNAL,
            "constraint.load_surge",
            0.35,
            "fraction of design load",
            "Tenant workload surges in one Data Hall, up to its design load. All of it becomes "
            "heat in that hall, and the UPS and transformers carry more.",
        ),
        FaultSpec(
            "utility.incomer_loss",
            "Utility incomer loss",
            "GPQM144",
            FaultCategory.EXTERNAL,
            "constraint.utility_loss",
            1.0,
            "loss",
            "The grid supply on this incomer fails (a voltage sag below half severity). The "
            "other transformer on the side carries its MSB; with both gone the ATS starts the "
            "gensets and transfers, and the UPS batteries bridge the gap.",
            where=incomers,
        ),
        FaultSpec(
            "genset.fail_to_start",
            "Fail to start",
            "Genset",
            FaultCategory.EQUIPMENT,
            "constraint.fail_to_start",
            1.0,
            "fail",
            "The engine cranks but does not fire, and shuts down on over-crank; the other "
            "gensets on its bus (N+1) carry the load.",
        ),
        FaultSpec(
            "ats.fail_to_transfer",
            "ATS fails to transfer",
            "GEM630",
            FaultCategory.EQUIPMENT,
            "constraint.ats_stuck",
            1.0,
            "stuck",
            "The main bus's ATS mechanism jams: it stays where it is, so on a utility loss "
            "the bus stays dead while its gensets run, and the UPS batteries run down.",
        ),
        FaultSpec(
            "breaker.trip",
            "Breaker trip",
            "Breaker",
            FaultCategory.EQUIPMENT,
            "constraint.breaker_trip",
            1.0,
            "trip",
            "A Demo Rack breaker opens (once the level passes half): its nine circuits lose "
            "supply and the meters behind it read zero. It recloses 30 s after the Clear.",
        ),
        FaultSpec(
            "breaker.earth_fault",
            "Earth fault",
            "Breaker",
            FaultCategory.EQUIPMENT,
            "constraint.earth_fault",
            1.0,
            "leak",
            "Leakage to earth on a Demo Rack circuit (once the level passes half): the "
            "breaker's EF bit sets and it trips on the fault, like a breaker trip.",
        ),
        FaultSpec(
            "e820.incomer_trip",
            "Incomer trip",
            "Production/E820",
            FaultCategory.EQUIPMENT,
            "constraint.incomer_trip",
            1.0,
            "trip",
            "The Demo Rack's own incomer trips (once the level passes half): every circuit "
            "and demo meter loses supply. The site is unaffected.",
        ),
        FaultSpec(
            "ups.rectifier_failure",
            "Rectifier failure",
            "UPS",
            FaultCategory.EQUIPMENT,
            "constraint.rectifier_failure",
            1.0,
            "fail",
            "The rectifier fails: the module runs on its battery until it is exhausted, then "
            "drops out and the other two modules in the hall take its share.",
        ),
        FaultSpec(
            "ups.battery_degradation",
            "Battery degradation",
            "UPS",
            FaultCategory.EQUIPMENT,
            "constraint.battery_fade",
            0.8,
            "fraction of capacity lost",
            "The battery strings age and lose capacity: nothing shows until the module is on "
            "battery, when its charge falls faster and its autonomy is shorter.",
            default_severity=0.5,
        ),
        *(
            FaultSpec(
                f"{type_id.lower()}.breaker_trip",
                "Breaker trip",
                type_id,
                FaultCategory.EQUIPMENT,
                "constraint.breaker_trip",
                1.0,
                "trip",
                "The breaker of the circuit this meter measures trips (once the level passes "
                "half): everything below it loses supply.",
            )
            for type_id in (*sorted(METER_TYPES), "BCPM")
        ),
        *(
            FaultSpec(
                f"{type_id.lower()}.comm_loss",
                "Communication loss",
                type_id,
                FaultCategory.COMMUNICATION,
                "quality.comm_loss",
                1.0,
                "fraction of polls lost",
                "The meter stops answering polls: its points go uncertain, then bad at half "
                "severity. The circuit itself carries on.",
            )
            for type_id in (*sorted(METER_TYPES), "BCPM")
        ),
        *_cooling_faults(),
        FaultSpec(
            "diesel.fuel_pump_failure",
            "Fuel pump failure",
            "Diesel",
            FaultCategory.EQUIPMENT,
            "constraint.pump_failure",
            1.0,
            "fail",
            "The tank's transfer pump trips: while its gensets run, their day tanks drain and "
            "are not refilled, until the engines shut down on low fuel.",
        ),
        FaultSpec(
            "ips.insulation_fault",
            "Insulation fault",
            "IPS",
            FaultCategory.EQUIPMENT,
            "constraint.insulation_loss",
            1.0,
            "fraction of insulation lost",
            "The isolated circuit's insulation to earth breaks down (damp, damaged cable): it "
            "falls from 10 MΩ towards 5 kΩ. The IPS insulation monitor reads every circuit "
            "in parallel and its value drops; below 50 kΩ the fault locator flags this circuit. "
            "The isolated supply keeps running, as an IT system is meant to on a first fault.",
        ),
        FaultSpec(
            "ips.ct_open",
            "Locator CT open",
            "IPS",
            FaultCategory.SENSOR,
            "observation.ct_open",
            1.0,
            "open",
            "The fault locator's current transformer on this circuit is disconnected (once the "
            "level passes half): the IPS raises No CT and can no longer locate an insulation "
            "fault on the circuit, although the monitor still measures it.",
        ),
        FaultSpec(
            "ips.ct_short",
            "Locator CT short-circuited",
            "IPS",
            FaultCategory.SENSOR,
            "observation.ct_short",
            1.0,
            "short",
            "The fault locator's current transformer on this circuit is short-circuited (once "
            "the level passes half): the IPS raises Short CT and can no longer locate an "
            "insulation fault on the circuit.",
        ),
        FaultSpec(
            "ips.pe_loss",
            "PE connection lost",
            "IPS",
            FaultCategory.EQUIPMENT,
            "constraint.pe_loss",
            1.0,
            "lost",
            "The protective-earth bond of this circuit's panel section opens (once the level "
            "passes half): the insulation monitor loses its reference to earth, raises PE "
            "Connection and cannot measure insulation until it is restored.",
        ),
        FaultSpec(
            "water.municipal_loss",
            "Municipal supply loss",
            "CW Ground Valve",
            FaultCategory.EXTERNAL,
            "constraint.supply_loss",
            1.0,
            "fraction of mains flow lost",
            "The mains supply to the site fails: the ground tanks stop refilling and drain as "
            "the transfer and AC makeup pumps draw on them.",
            where=lambda design: (water_layout(design).municipal,),
        ),
        FaultSpec(
            "ground_valve.stuck",
            "Valve stuck",
            "CW Ground Valve",
            FaultCategory.EQUIPMENT,
            "constraint.stuck",
            1.0,
            "stuck",
            "A ground tank inlet valve's actuator seizes and it holds its position whatever "
            "it is commanded (once the level passes half): the tank stops floating on level.",
            where=lambda design: water_layout(design).ground_inlets,
        ),
        FaultSpec(
            "roof_valve.stuck",
            "Valve stuck",
            "CW Roof Valve",
            FaultCategory.EQUIPMENT,
            "constraint.stuck",
            1.0,
            "stuck",
            "A roof tank inlet valve's actuator seizes and it holds its position whatever it "
            "is commanded (once the level passes half): stuck open, it can overfill its tank.",
            where=lambda design: water_layout(design).roof_inlets,
        ),
        FaultSpec(
            "transfer_pump.trip",
            "Transfer pump trip",
            "CW Transfer Pump",
            FaultCategory.EQUIPMENT,
            "constraint.trip",
            1.0,
            "trip",
            "The pump trips on overload (once the level passes half). The standby pump takes "
            "its place; with all three lost the roof tanks drain at what the towers evaporate.",
        ),
        FaultSpec(
            "roof_tank.level_sensor",
            "Level sensor reads high",
            "CW Roof Tank",
            FaultCategory.SENSOR,
            "observation.level_offset_pct",
            40.0,
            "% of level",
            "The roof tank's level transmitter reads high. The water is unchanged, but the "
            "transfer pumps, controlled on the reading, start late or not at all.",
        ),
        FaultSpec(
            "makeup_pump.failure",
            "Makeup pump failure",
            "Makeup Water Pump",
            FaultCategory.EQUIPMENT,
            "constraint.trip",
            1.0,
            "trip",
            "The cell's makeup pump trips (once the level passes half): its basin falls with "
            "evaporation until the cell trips on low basin level.",
        ),
        FaultSpec(
            "leak.pipe_leak",
            "Pipe leak",
            "Water Leak Cable Sensor",
            FaultCategory.EQUIPMENT,
            "constraint.leak_lps",
            2.0,
            "L/s",
            "A pipe leaks in the room this cable runs under. Water pools on the floor and the "
            "cable alarms, with its position, once the water reaches it; the closed CHW loop "
            "(or, in a water plant room, the tanks) loses the water, until it runs dry.",
            where=lambda design: tuple(water_layout(design).cable_source),
        ),
        *_life_safety_faults(),
    ]
)
"""The catalog, consumed by the device models in `sim`."""
