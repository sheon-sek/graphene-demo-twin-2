"""World state: one AssetState per Plant Design node, at one whole sim second."""

import hashlib
import json
from dataclasses import dataclass, field

type Scalar = float | int | bool | str | None
type AssetState = dict[str, Scalar]
"""The physical state of one asset (or room) at one instant: named scalar variables."""


@dataclass(slots=True)
class WorldState:
    time: int
    """Sim time in whole seconds since the Unix epoch; the OPC UA SourceTimestamp."""
    assets: dict[str, AssetState]
    """AssetState keyed by Plant Design node: an asset path, `~<name>` or a room id."""
    faults: dict[str, dict[str, Scalar]] = field(default_factory=dict)
    """The active faults, keyed `<fault>@<asset>`: what was injected, when, and its level now."""

    def copy(self) -> "WorldState":
        """An independent copy; every value is a scalar, so one level deep suffices."""
        return WorldState(
            self.time,
            {node: dict(s) for node, s in self.assets.items()},
            {key: dict(f) for key, f in self.faults.items()},
        )


def state_hash(state: WorldState) -> str:
    """SHA-256 over the exact bits of every value, independent of dict insertion order."""
    h = hashlib.sha256(f"t{state.time}\n".encode())
    for node in sorted(state.assets):
        variables = state.assets[node]
        for name in sorted(variables):
            h.update(f"{json.dumps(node)}.{json.dumps(name)}={_encode(variables[name])}\n".encode())
    for key in sorted(state.faults):
        fault = state.faults[key]
        for name in sorted(fault):
            h.update(f"!{json.dumps(key)}.{json.dumps(name)}={_encode(fault[name])}\n".encode())
    return h.hexdigest()


def _encode(value: Scalar) -> str:
    match value:
        case None:
            return "n"
        case bool():
            return f"b{int(value)}"
        case int():
            return f"i{value}"
        case float():
            return f"f{value.hex()}"
        case str():
            return f"s{json.dumps(value)}"
    raise TypeError(f"AssetState values must be scalars, got {type(value).__name__}")
