"""The fault catalog: named failure mechanisms bound to an asset type, and their parameters."""

import math
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from graphene_demo_twin.plant_design import ConnectionKind, PlantDesign
from graphene_demo_twin.sim import Event, EventError
from graphene_demo_twin.sim.electrical import METER_TYPES, incomers

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
            "The device stops forwarding: it and everything downstream of it on the network "
            "lose quality.",
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
    ]
)
"""The catalog, consumed by the device models in `sim` (the stand-ins in `sim.placeholder`
until their P1 and P2 physics arrive)."""
