from __future__ import annotations
from datetime import datetime, timezone
from hashlib import sha256


def stable_unit(seed: str, key: str, timestamp: datetime, bucket_seconds: int = 60) -> float:
    if timestamp.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    bucket = int(timestamp.astimezone(timezone.utc).timestamp()) // bucket_seconds
    digest = sha256(f"{seed}|{key}|{bucket}".encode()).digest()
    raw = int.from_bytes(digest[:8], "big") / (2**64 - 1)
    return raw * 2.0 - 1.0


def stable_fraction(seed: str, key: str) -> float:
    digest = sha256(f"{seed}|{key}".encode()).digest()
    return int.from_bytes(digest[:8], "big") / (2**64 - 1)
