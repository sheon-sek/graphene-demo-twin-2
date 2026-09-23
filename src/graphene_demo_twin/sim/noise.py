"""The world's only source of randomness: deterministic noise keyed by seed, name and sim time."""

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

    def gauss(self, key: str, time: int) -> float:
        """A standard normal value (Box-Muller over two keyed uniforms)."""
        u1 = 1.0 - self.uniform(f"{key}#1", time)  # (0, 1], safe for log
        u2 = self.uniform(f"{key}#2", time)
        return math.sqrt(-2.0 * math.log(u1)) * math.cos(_TWO_PI * u2)
