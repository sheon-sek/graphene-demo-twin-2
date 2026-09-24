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


HAND_AUTO: tuple[CommandSpec, ...] = (
    CommandSpec("mode", "Hand / auto", "mode", choices=("auto", "hand")),
    CommandSpec("run", "Start / stop (hand)", "hand_run", kind="switch"),
)
"""The selector every Controller-driven unit carries: in auto the Controller runs it, in hand
the operator's start/stop does."""
HAND_AUTO_STATE: dict[str, str | bool] = {"mode": "auto", "hand_run": True}
"""A unit's selector as it starts: in auto, with the hand switch left on."""


def selected_run(s: Mapping[str, Any], auto_run: bool) -> bool:
    """Whether a unit is asked to run: by its Controller in auto, by the operator in hand."""
    return bool(s["hand_run"]) if s["mode"] == "hand" else auto_run


def apply_command(specs: Iterable[CommandSpec], params: Mapping[str, Any], s: dict) -> None:
    """Take a checked Operator Command into the unit's AssetState."""
    spec = next(c for c in specs if c.name == params["command"])
    value = params["value"]
    s[spec.variable] = float(value) if spec.kind == "number" else value
