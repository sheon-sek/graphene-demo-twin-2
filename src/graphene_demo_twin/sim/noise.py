"""The world's only source of randomness: deterministic noise keyed by seed, name and sim time."""

import functools
import hashlib
import math

_TWO_PI = 2.0 * math.pi
_UNIT = 2.0**-53


class Noise:
    """Stateless seeded noise. A draw depends only on (seed, key, time), never on call order.

    Because it holds no state, the Live World and every What-if Fork draw exactly the same
    values at the same sim time, and adding or reordering draws elsewhere changes nothing.
    """

    __slots__ = ("seed",)

    def __init__(self, seed: int) -> None:
        self.seed = seed

    def uniform(self, key: str, time: int) -> float:
        """A value in [0, 1)."""
        digest = hashlib.blake2b(f"{self.seed}|{key}|{time}".encode(), digest_size=8).digest()
        return (int.from_bytes(digest) >> 11) * _UNIT

    def held(self, key: str, index: int) -> float:
        """A value in [0, 1) held for a whole period, such as one per day; memoised."""
        return _held(self.seed, key, index)

    def smooth(self, key: str, time: int, period: int) -> float:
        """Slowly varying noise in [-1, 1]: seeded values every `period` seconds, eased
        smoothly between. Like every draw here, it depends only on (seed, key, time)."""
        knot, into = divmod(time, period)
        a = _knot(self.seed, key, knot)
        b = _knot(self.seed, key, knot + 1)
        x = into / period
        return a + (b - a) * x * x * (3.0 - 2.0 * x)

    def gauss(self, key: str, time: int) -> float:
        """A standard normal value (Box-Muller over two keyed uniforms)."""
        u1 = 1.0 - self.uniform(f"{key}#1", time)  # (0, 1], safe for log
        u2 = self.uniform(f"{key}#2", time)
        return math.sqrt(-2.0 * math.log(u1)) * math.cos(_TWO_PI * u2)


@functools.lru_cache(maxsize=4096)
def _knot(seed: int, key: str, knot: int) -> float:
    """One smooth-noise knot in [-1, 1]; memoised, since neighbouring seconds share it."""
    return 2.0 * _held(seed, f"{key}~", knot) - 1.0


@functools.lru_cache(maxsize=4096)
def _held(seed: int, key: str, index: int) -> float:
    return Noise(seed).uniform(key, index)
