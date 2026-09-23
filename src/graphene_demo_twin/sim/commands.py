"""Operator Commands: manual actions on one asset (hand/auto, start/stop, setpoints)."""

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

COMMAND = "command"
"""Event kind of an Operator Command; params are `{command, value}`."""


@dataclass(frozen=True, slots=True)
class CommandSpec:
    """One command an operator can give an asset type."""

    name: str
    label: str
    variable: str
    """The AssetState variable that holds the commanded value."""
    kind: Literal["choice", "switch", "number"] = "choice"
    choices: tuple[str, ...] = ()
    minimum: float | None = None
    maximum: float | None = None
    unit: str = ""

    def problem(self, value: Any) -> str | None:
        """Why `value` cannot be commanded, or None if it can."""
        match self.kind:
            case "choice":
                if value not in self.choices:
                    return f"{self.name} must be one of {list(self.choices)}, got {value!r}"
            case "switch":
                if not isinstance(value, bool):
                    return f"{self.name} must be true or false, got {value!r}"
            case "number":
                if (
                    isinstance(value, bool)
                    or not isinstance(value, int | float)
                    or not math.isfinite(value)
                ):
                    return f"{self.name} must be a number, got {value!r}"
                if not self.minimum <= value <= self.maximum:
                    return f"{self.name} must be in [{self.minimum}, {self.maximum}] {self.unit}"
        return None


def command_problem(specs: Iterable[CommandSpec], params: Mapping[str, Any]) -> str | None:
    """Why `params` is not a valid command among `specs`, or None if it is."""
    if params.keys() != {"command", "value"}:
        return "an Operator Command takes exactly {command, value}"
    for spec in specs:
        if spec.name == params["command"]:
            return spec.problem(params["value"])
    return f"unknown command: {params['command']!r}"
