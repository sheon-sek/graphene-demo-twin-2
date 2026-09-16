from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def load_json(relative: str):
    with (ROOT / relative).open(encoding="utf-8") as fh:
        return json.load(fh)


def world_config() -> dict:
    return load_json("config/world/default.json")


def physics_config() -> dict:
    return load_json("config/physics/default.json")


def fault_catalog() -> dict:
    return load_json("config/scenarios/fault-catalog.json")


def demo_scenarios() -> dict:
    return load_json("config/scenarios/demo.json")
