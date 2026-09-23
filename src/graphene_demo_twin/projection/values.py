"""Point values as every surface carries them: typed to the point's Ignition data type."""

import json
import math
import struct
from enum import StrEnum
from typing import Any

from graphene_demo_twin.asset_model import Point
from graphene_demo_twin.sim import Scalar


class Quality(StrEnum):
    """OPC UA quality of a point value (Good, Uncertain, Bad)."""

    GOOD = "good"
    UNCERTAIN = "uncertain"
    BAD = "bad"


_INT_RANGE = {"Int4": (-(2**31), 2**31 - 1), "Int8": (-(2**63), 2**63 - 1)}
_ZERO: dict[str, Scalar] = {
    "Float4": 0.0,
    "Float8": 0.0,
    "Int4": 0,
    "Int8": 0,
    "Boolean": False,
    "String": "",
    "DateTime": 0,
    "DataSet": "",
    "Document": "",
}


def type_default(data_type: str) -> Scalar:
    """The zero of an Ignition data type."""
    return _ZERO[data_type]


def coerce(value: Any, data_type: str) -> Scalar:
    """`value` as the exact value a point of `data_type` carries on every surface.

    Float4 is rounded to 32 bits so REST, SSE and OPC UA show identical numbers. DateTime is
    epoch milliseconds, as the export writes it; DataSet and Document are JSON text. Raises
    ValueError for anything that is not a finite value of the type.
    """
    match data_type:
        case "Float4" | "Float8":
            if isinstance(value, bool) or not isinstance(value, int | float):
                raise ValueError(f"not a number: {value!r}")
            x = float(value)
            if not math.isfinite(x):
                raise ValueError(f"not finite: {x!r}")
            if data_type == "Float4":
                try:
                    x = struct.unpack("f", struct.pack("f", x))[0]
                except OverflowError:
                    raise ValueError(f"out of Float4 range: {x!r}") from None
            return x
        case "Int4" | "Int8" | "DateTime":
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError(f"not finite: {value!r}")
            if not isinstance(value, int | float):
                raise ValueError(f"not a number: {value!r}")
            lo, hi = _INT_RANGE.get(data_type, _INT_RANGE["Int8"])
            return min(max(round(value), lo), hi)
        case "Boolean":
            if not isinstance(value, bool | int | float):
                raise ValueError(f"not a boolean: {value!r}")
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError(f"not finite: {value!r}")
            return bool(value)
        case "String":
            if isinstance(value, dict | list):
                raise ValueError(f"not a string: {value!r}")
            return "" if value is None else str(value)
        case "DataSet" | "Document":
            if isinstance(value, str):
                return value
            return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    raise ValueError(f"unknown data type: {data_type}")


def fallback_value(point: Point) -> Scalar:
    """The Compatibility Fallback for a point outside the modelled world: the value the export
    configures for it, or its data type's zero. Constant, so a Causal Dead End by definition.
    """
    raw = point.export_value
    if raw is None or (isinstance(raw, dict) and "bindType" in raw):
        return type_default(point.data_type)
    try:
        return coerce(raw, point.data_type)
    except ValueError:
        return type_default(point.data_type)
