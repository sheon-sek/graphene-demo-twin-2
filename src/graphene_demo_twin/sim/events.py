"""The Event Log's entries: sim-timestamped operator actions."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any


class EventError(ValueError):
    """An event cannot be added to the Event Log."""


@dataclass(frozen=True, slots=True)
class Event:
    """One operator action (fault inject/clear, Operator Command) in the Event Log.

    It takes effect at the start of the step out of sim second `at`, after any earlier-logged
    event at the same second.
    """

    at: int
    """Sim time in whole seconds since the Unix epoch."""
    kind: str
    """`fault.inject`, `fault.clear`, `command`, …; the domain that handles it gives meaning."""
    target: str
    """The Plant Design node acted on."""
    params: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.at, int) or isinstance(self.at, bool):
            raise EventError(f"event time must be whole sim seconds, got {self.at!r}")
        object.__setattr__(self, "params", MappingProxyType(dict(self.params)))
