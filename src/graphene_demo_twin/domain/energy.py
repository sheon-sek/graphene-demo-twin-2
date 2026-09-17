from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timedelta
from threading import RLock
from typing import Callable


ConstraintMap = dict[str, dict[str, float]]


@dataclass(frozen=True)
class ConstraintWindow:
    start: datetime
    end: datetime
    constraints: ConstraintMap


@dataclass(frozen=True)
class _PowerTable:
    prefix_kwh: dict[str, tuple[float, ...]]


_TABLE_CACHE: OrderedDict[tuple[str, tuple], _PowerTable] = OrderedDict()
_TABLE_CACHE_LOCK = RLock()
_TABLE_CACHE_LIMIT = 8


def _constraint_key(constraints: ConstraintMap | None) -> tuple:
    return tuple(
        (path.lower(), tuple(sorted((name, float(value)) for name, value in values.items())))
        for path, values in sorted((constraints or {}).items())
    )


class PeriodicPowerIntegrator:
    """Random-access integral for a periodic, fixed-step deterministic power primitive.

    The cache is performance-only: every table is a pure function of ``cache_key``,
    constraints, timestamp bucket, and the supplied power sampler. It is never used as
    simulation state and can be discarded without changing results.
    """

    def __init__(
        self,
        *,
        epoch: datetime,
        step_seconds: int,
        period_seconds: int,
        asset_paths: set[str],
        cache_key: str,
        power_sampler: Callable[[datetime, ConstraintMap], dict[str, float]],
    ):
        if step_seconds <= 0:
            raise ValueError("energy step must be >0")
        if period_seconds <= 0 or period_seconds % step_seconds:
            raise ValueError("energy repeat period must be a positive multiple of step")
        self.epoch = epoch
        self.step_seconds = int(step_seconds)
        self.period_seconds = int(period_seconds)
        self.steps_per_period = self.period_seconds // self.step_seconds
        self.asset_paths = tuple(sorted(path.lower() for path in asset_paths))
        self.cache_key = cache_key
        self.power_sampler = power_sampler

    def primitive_kwh(
        self,
        asset_path: str,
        timestamp: datetime,
        constraints: ConstraintMap | None = None,
    ) -> float:
        """Integral from model epoch to ``timestamp`` under one constant constraint set."""
        elapsed = (timestamp - self.epoch).total_seconds()
        if elapsed < 0:
            raise ValueError("energy timestamp is before model epoch")
        asset_key = asset_path.lower()
        if asset_key not in self.asset_paths:
            return 0.0

        table = self._table(constraints or {})
        full_periods = int(elapsed // self.period_seconds)
        remainder = elapsed - full_periods * self.period_seconds
        full_steps = int(remainder // self.step_seconds)
        partial_seconds = remainder - full_steps * self.step_seconds

        prefix = table.prefix_kwh[asset_key]
        result = full_periods * prefix[-1] + prefix[full_steps]
        if partial_seconds > 0:
            sample_time = self.epoch + timedelta(seconds=full_steps * self.step_seconds)
            power_kw = self.power_sampler(sample_time, constraints or {}).get(asset_key, 0.0)
            result += power_kw * partial_seconds / 3600.0
        return result

    def energy_kwh(
        self,
        asset_path: str,
        timestamp: datetime,
        windows: list[ConstraintWindow] | None = None,
    ) -> float:
        """Baseline integral plus exact corrections for piecewise-constant constraint windows."""
        result = self.primitive_kwh(asset_path, timestamp)
        for window in windows or []:
            start = max(self.epoch, window.start)
            end = min(timestamp, window.end)
            if end <= start:
                continue
            constrained = self.primitive_kwh(asset_path, end, window.constraints)
            constrained -= self.primitive_kwh(asset_path, start, window.constraints)
            baseline = self.primitive_kwh(asset_path, end)
            baseline -= self.primitive_kwh(asset_path, start)
            result += constrained - baseline
        return max(0.0, result)

    def _table(self, constraints: ConstraintMap) -> _PowerTable:
        key = (self.cache_key, _constraint_key(constraints))
        with _TABLE_CACHE_LOCK:
            cached = _TABLE_CACHE.get(key)
            if cached is not None:
                _TABLE_CACHE.move_to_end(key)
                return cached

            prefixes = {path: [0.0] * (self.steps_per_period + 1) for path in self.asset_paths}
            step_hours = self.step_seconds / 3600.0
            for index in range(self.steps_per_period):
                sample_time = self.epoch + timedelta(seconds=index * self.step_seconds)
                powers = self.power_sampler(sample_time, constraints)
                for path in self.asset_paths:
                    prefixes[path][index + 1] = prefixes[path][index] + max(
                        0.0, float(powers.get(path, 0.0))
                    ) * step_hours

            built = _PowerTable({path: tuple(values) for path, values in prefixes.items()})
            _TABLE_CACHE[key] = built
            _TABLE_CACHE.move_to_end(key)
            while len(_TABLE_CACHE) > _TABLE_CACHE_LIMIT:
                _TABLE_CACHE.popitem(last=False)
            return built
