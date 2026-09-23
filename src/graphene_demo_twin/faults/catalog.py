"""The fault catalog: named failure mechanisms bound to an asset type, and their parameters."""

import math
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from graphene_demo_twin.plant_design import PlantDesign
from graphene_demo_twin.sim import Event, EventError

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


_PREFIX = {
    Mechanism.PHYSICAL_CONSTRAINT: "constraint",
    Mechanism.OBSERVATION: "observation",
    Mechanism.QUALITY: "quality",
    Mechanism.CONTROLLER: "controller",
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

    @property
    def mechanism(self) -> Mechanism:
        return MECHANISM_OF[self.category]


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
        return None


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
        FaultSpec(
            "th.drift",
            "Sensor drift",
            "Temperature and Humidity",
            FaultCategory.SENSOR,
            "observation.bias_c",
            6.0,
            "°C",
            "The temperature element drifts high; the room itself is unchanged.",
            default_severity=0.5,
        ),
        FaultSpec(
            "th.stuck",
            "Stuck reading",
            "Temperature and Humidity",
            FaultCategory.SENSOR,
            "observation.stuck",
            1.0,
            "stuck",
            "The sensor freezes on its last reading (once the level passes half).",
        ),
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
    ]
)
"""The P0 catalog, consumed by the stand-in device models in `sim.placeholder`."""
