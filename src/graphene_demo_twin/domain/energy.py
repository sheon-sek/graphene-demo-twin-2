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
_POWER_SAMPLE_CACHE: OrderedDict[tuple, dict[str, float]] = OrderedDict()
_POWER_SAMPLE_CACHE_LOCK = RLock()
_POWER_SAMPLE_CACHE_LIMIT = 32768


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
        self.power_sampler = self._memoize_power_sampler(power_sampler)

    def _memoize_power_sampler(
        self,
        power_sampler: Callable[[datetime, ConstraintMap], dict[str, float]],
    ) -> Callable[[datetime, ConstraintMap], dict[str, float]]:
        cache_key = self.cache_key
        cache = _POWER_SAMPLE_CACHE
        lock = _POWER_SAMPLE_CACHE_LOCK
        limit = _POWER_SAMPLE_CACHE_LIMIT

        def sample(timestamp: datetime, constraints: ConstraintMap) -> dict[str, float]:
            key = (cache_key, timestamp, _constraint_key(constraints))
            with lock:
                value = cache.get(key)
                if value is not None:
                    cache.move_to_end(key)
                    return value
            value = power_sampler(timestamp, constraints)
            with lock:
                cache[key] = value
                cache.move_to_end(key)
                while len(cache) > limit:
                    cache.popitem(last=False)
            return value

        return sample

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
            constrained = self._window_kwh(asset_path, start, end, window.constraints)
            baseline = self.primitive_kwh(asset_path, end)
            baseline -= self.primitive_kwh(asset_path, start)
            result += constrained - baseline
        return max(0.0, result)

    def _window_kwh(
        self,
        asset_path: str,
        start: datetime,
        end: datetime,
        constraints: ConstraintMap,
    ) -> float:
        """Exact ``primitive_kwh(end, C) - primitive_kwh(start, C)``.

        Samples only the grid steps inside ``[start, end)`` instead of
        building the full period table per constraint key, which is what
        keeps a long discretized ramp (thousands of distinct-severity
        windows) tractable. The sample times are the same ``(j % N) * step``
        slots the period table uses, so the result is identical to the
        table-based primitive pair. Falls back to that pair when the window
        spans a full period, where the table is the cheaper exact path.
        """
        asset_key = asset_path.lower()
        if asset_key not in self.asset_paths:
            return 0.0
        step = self.step_seconds
        s = (start - self.epoch).total_seconds()
        e = (end - self.epoch).total_seconds()
        fp_s, rem_s = divmod(s, self.period_seconds)
        fs_s, ps_s = divmod(rem_s, step)
        fp_e, rem_e = divmod(e, self.period_seconds)
        fs_e, ps_e = divmod(rem_e, step)
        j_s = int(fp_s) * self.steps_per_period + int(fs_s)
        j_e = int(fp_e) * self.steps_per_period + int(fs_e)
        if j_e - j_s > self.steps_per_period:
            return self.primitive_kwh(asset_path, end, constraints) - self.primitive_kwh(
                asset_path, start, constraints
            )
        step_hours = step / 3600.0
        total = 0.0
        for j in range(j_s, j_e):
            sample_time = self.epoch + timedelta(
                seconds=(j % self.steps_per_period) * step
            )
            power_kw = self.power_sampler(sample_time, constraints).get(asset_key, 0.0)
            total += max(0.0, float(power_kw)) * step_hours
        if ps_e > 0:
            sample_time = self.epoch + timedelta(seconds=int(fs_e) * step)
            total += float(
                self.power_sampler(sample_time, constraints).get(asset_key, 0.0)
            ) * (ps_e / 3600.0)
        if ps_s > 0:
            sample_time = self.epoch + timedelta(seconds=int(fs_s) * step)
            total -= float(
                self.power_sampler(sample_time, constraints).get(asset_key, 0.0)
            ) * (ps_s / 3600.0)
        return total

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
