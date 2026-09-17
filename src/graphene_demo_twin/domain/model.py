from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from typing import Any

from graphene_demo_twin.config import physics_config, world_config
from graphene_demo_twin.domain.energy import ConstraintWindow, PeriodicPowerIntegrator
from graphene_demo_twin.domain.physical import PhysicalWorld, PhysicalWorldSolver


def parse_utc(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return dt.astimezone(timezone.utc)


def deterministic_noise(seed: str, signal: str, dt: datetime, bucket_seconds: int = 60) -> float:
    bucket = int(dt.timestamp()) // bucket_seconds
    raw = hashlib.sha256(f"{seed}|{signal}|{bucket}".encode()).digest()
    return int.from_bytes(raw[:8], "big") / (2**64 - 1) * 2 - 1


def periodic_deterministic_noise(
    seed: str,
    signal: str,
    dt: datetime,
    epoch: datetime,
    bucket_seconds: int,
    period_seconds: int,
) -> float:
    period_buckets = period_seconds // bucket_seconds
    bucket = int((dt - epoch).total_seconds()) // bucket_seconds
    periodic_bucket = bucket % period_buckets
    raw = hashlib.sha256(f"{seed}|periodic|{signal}|{periodic_bucket}".encode()).digest()
    return int.from_bytes(raw[:8], "big") / (2**64 - 1) * 2 - 1


def _day_fraction(dt: datetime) -> float:
    return (dt.hour * 3600 + dt.minute * 60 + dt.second) / 86400


def _interp_profile(hour: float, anchors: list[tuple[float, float]]) -> float:
    points = anchors + [(anchors[0][0] + 24, anchors[0][1])]
    h = hour
    if h < anchors[0][0]:
        h += 24
    for (h0, v0), (h1, v1) in zip(points, points[1:]):
        if h0 <= h <= h1:
            fraction = (h - h0) / (h1 - h0)
            return v0 + (v1 - v0) * fraction
    return anchors[-1][1]


@dataclass(frozen=True)
class BaseWorldSnapshot:
    timestamp: datetime
    mode: str
    site: dict[str, Any]
    signals: dict[str, Any]
    world: PhysicalWorld | None = None


class DomainModel:
    def __init__(self, manifest: dict, topology: dict, seed: str | None = None):
        self.manifest = manifest
        self.topology = topology
        self.cfg = world_config()
        self.physics = physics_config()
        self.seed = seed or self.cfg["seed"]
        self.epoch = parse_utc(self.cfg["modelEpochUtc"])
        self.energy_step_seconds = int(self.physics.get("energy", {}).get("stepSeconds", 300))
        self.energy_period_seconds = int(
            self.physics.get("energy", {}).get("repeatPeriodSeconds", 7 * 86400)
        )
        if self.energy_period_seconds % self.energy_step_seconds:
            raise ValueError("energy repeat period must be a multiple of energy step")
        self.world_solver = PhysicalWorldSolver(topology, self.physics)

        probe = self.world_solver.solve(self.epoch, self._site_seed(self.epoch), {})
        self.authoritative_energy_assets = {
            point["instancePath"].lower()
            for point in self.manifest["points"]
            if point.get("sourceClass") == "ENERGY_INTEGRAL"
            and point.get("instancePath")
            and probe.asset(point["instancePath"]) is not None
        }
        self.energy_integrator = PeriodicPowerIntegrator(
            epoch=self.epoch,
            step_seconds=self.energy_step_seconds,
            period_seconds=self.energy_period_seconds,
            asset_paths=self.authoritative_energy_assets,
            cache_key=self._energy_cache_key(),
            power_sampler=self._physical_power_sample,
        )

    def calculate(
        self,
        timestamp: str | datetime,
        world_profile: str = "demo",
        constraints: dict[str, dict[str, float]] | None = None,
        constraint_windows: list[ConstraintWindow] | None = None,
    ) -> BaseWorldSnapshot:
        dt = parse_utc(timestamp)
        if dt < self.epoch:
            raise ValueError(f"timestamp before model epoch {self.epoch.isoformat()}")

        site_seed = self._site_seed(dt)
        world = self.world_solver.solve(dt, site_seed, constraints)
        site = world.site
        signals = {}
        for point in self.manifest["points"]:
            signals[point["signalKey"]] = self._value_for(
                point,
                dt,
                site,
                world,
                constraint_windows,
            )
        return BaseWorldSnapshot(dt, world_profile, site, signals, world)

    def _energy_cache_key(self) -> str:
        serves = [
            (relation.get("from"), relation.get("to"))
            for relation in self.topology.get("relations", [])
            if relation.get("kind") == "serves"
        ]
        assets = [
            (asset.get("assetId"), asset.get("typeId"), asset.get("exportPath"))
            for asset in self.topology.get("assets", [])
        ]
        payload = {
            "seed": self.seed,
            "physics": self.physics,
            "energyStepSeconds": self.energy_step_seconds,
            "energyPeriodSeconds": self.energy_period_seconds,
            "energyAssets": sorted(self.authoritative_energy_assets),
            "assets": assets,
            "serves": serves,
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    def _physics_bucket(self, dt: datetime) -> datetime:
        elapsed = max(0.0, (dt - self.epoch).total_seconds())
        bucket = int(elapsed // self.energy_step_seconds)
        return self.epoch + timedelta(seconds=bucket * self.energy_step_seconds)

    def _periodic_noise(self, signal: str, dt: datetime, bucket_seconds: int) -> float:
        return periodic_deterministic_noise(
            self.seed,
            signal,
            dt,
            self.epoch,
            bucket_seconds,
            self.energy_period_seconds,
        )

    def _site_seed(self, dt: datetime) -> dict[str, float]:
        """Periodic fixed-step physical driver shared by power and its energy primitive."""
        driver_dt = self._physics_bucket(dt)
        local_hour = (
            driver_dt.hour + 8 + driver_dt.minute / 60 + driver_dt.second / 3600
        ) % 24
        demand_seed_kw = _interp_profile(
            local_hour,
            [(0, 620), (6, 690), (9, 820), (13, 1030), (15, 1080), (19, 910), (23, 670)],
        )
        slow = 1 + 0.018 * math.sin(
            (driver_dt - self.epoch).total_seconds() / 86400 / 7 * 2 * math.pi
        )
        demand_seed_kw *= slow * (
            1 + 0.01 * self._periodic_noise("site.plant_load", driver_dt, 300)
        )
        it_kw = demand_seed_kw * 2.35
        outside = 29 + 3.5 * math.sin((_day_fraction(driver_dt) - 0.25) * 2 * math.pi)
        outside += 0.35 * self._periodic_noise("weather.temp", driver_dt, 600)
        hall_a = 23.1 + 0.25 * math.sin(_day_fraction(driver_dt) * 2 * math.pi)
        hall_a += 0.08 * self._periodic_noise("hall.a.temp", driver_dt, 300)
        hall_b = 23.3 + 0.23 * math.sin((_day_fraction(driver_dt) + 0.03) * 2 * math.pi)
        hall_b += 0.08 * self._periodic_noise("hall.b.temp", driver_dt, 300)
        hall_a_rh = 51.5 + 1.8 * math.sin((_day_fraction(driver_dt) + 0.1) * 2 * math.pi)
        hall_a_rh += 0.3 * self._periodic_noise("hall.a.rh", driver_dt, 300)
        hall_b_rh = 52.0 + 1.7 * math.sin((_day_fraction(driver_dt) + 0.08) * 2 * math.pi)
        hall_b_rh += 0.3 * self._periodic_noise("hall.b.rh", driver_dt, 300)

        return {
            "plantLoadKw": round(demand_seed_kw, 3),
            "itLoadKw": round(it_kw, 3),
            "facilityLoadKw": round(it_kw + demand_seed_kw + 180, 3),
            "pue": round((it_kw + demand_seed_kw + 180) / it_kw, 3),
            "outsideTempC": round(outside, 3),
            "hallATempC": round(hall_a, 3),
            "hallBTempC": round(hall_b, 3),
            "hallARhPct": round(hall_a_rh, 3),
            "hallBRhPct": round(hall_b_rh, 3),
        }

    def _physical_power_sample(
        self,
        timestamp: datetime,
        constraints: dict[str, dict[str, float]],
    ) -> dict[str, float]:
        world = self.world_solver.solve(timestamp, self._site_seed(timestamp), constraints)
        return {
            path: world.asset(path).power_kw if world.asset(path) is not None else 0.0
            for path in self.authoritative_energy_assets
        }

    def _asset_scalar(self, asset: str, low: float, high: float) -> float:
        raw = hashlib.sha256(f"{self.seed}|asset|{asset}".encode()).digest()
        return low + (high - low) * (int.from_bytes(raw[:8], "big") / (2**64 - 1))

    def _meter_metrics(self, asset: str, dt: datetime) -> dict[str, float]:
        phase = []
        for index in range(3):
            base = self._asset_scalar(f"{asset}|p{index}", 18, 115)
            daily = 1 + 0.12 * math.sin(
                (_day_fraction(dt) - 0.18 + index * 0.015) * 2 * math.pi
            )
            p = max(
                0.1,
                base
                * daily
                * (1 + 0.015 * deterministic_noise(self.seed, f"{asset}.phase{index}.p", dt, 300)),
            )
            pf = 0.94 + 0.018 * deterministic_noise(
                self.seed, f"{asset}.phase{index}.pf", dt, 600
            )
            voltage = 230 * (
                1 + 0.006 * deterministic_noise(self.seed, f"{asset}.phase{index}.v", dt, 300)
            )
            apparent = p / pf
            reactive = math.sqrt(max(0, apparent * apparent - p * p))
            current = p * 1000 / max(1, voltage * pf)
            phase.append({"p": p, "pf": pf, "v": voltage, "s": apparent, "q": reactive, "i": current})
        total_p = sum(row["p"] for row in phase)
        total_s = sum(row["s"] for row in phase)
        total_q = sum(row["q"] for row in phase)
        return {
            "P1": phase[0]["p"],
            "P2": phase[1]["p"],
            "P3": phase[2]["p"],
            "Ptot": total_p,
            "Q1": phase[0]["q"],
            "Q2": phase[1]["q"],
            "Q3": phase[2]["q"],
            "Qtot": total_q,
            "S1": phase[0]["s"],
            "S2": phase[1]["s"],
            "S3": phase[2]["s"],
            "Stot": total_s,
            "PF1": phase[0]["pf"],
            "PF2": phase[1]["pf"],
            "PF3": phase[2]["pf"],
            "PFsys": total_p / total_s if total_s else 1,
            "I1": phase[0]["i"],
            "I2": phase[1]["i"],
            "I3": phase[2]["i"],
            "In": abs(phase[0]["i"] - phase[1]["i"]) + abs(phase[2]["i"] - phase[1]["i"]),
            "Isys": sum(row["i"] for row in phase) / 3,
            "V1": phase[0]["v"],
            "V2": phase[1]["v"],
            "V3": phase[2]["v"],
            "V12": phase[0]["v"] * math.sqrt(3),
            "V23": phase[1]["v"] * math.sqrt(3),
            "V31": phase[2]["v"] * math.sqrt(3),
            "Vsys": sum(row["v"] for row in phase) / 3,
            "Vsys2": sum(row["v"] for row in phase) / 3,
            "Hz": 50 + 0.02 * deterministic_noise(self.seed, f"{asset}.hz", dt, 300),
        }

    def _chiller_metrics(self, asset: str, dt: datetime, site: dict) -> dict[str, float]:
        ordinal = max(1, int(self._asset_scalar(asset, 1, 4)))
        share = site["plantLoadKw"] / 3 * (0.97 + 0.03 * ordinal)
        power = share * (1 + 0.01 * deterministic_noise(self.seed, f"{asset}.power", dt, 300))
        cop = 4.44 + 0.025 * deterministic_noise(self.seed, f"{asset}.cop", dt, 600)
        cooling = power * cop
        return {
            "Input Power": power,
            "Load": min(100, 100 * cooling / 1450),
            "Cooling Output": cooling,
            "COP": cop,
            "kW per RT": 3.517 / cop,
            "CHW Supply Temperature": 5.3
            + 0.05 * deterministic_noise(self.seed, f"{asset}.chws", dt, 300),
            "CHW Return Temperature": 11.2
            + 0.1 * deterministic_noise(self.seed, f"{asset}.chwr", dt, 300),
            "CHW Flow": max(0.1, cooling / (4.186 * 5.9)),
            "CW Supply Temperature": 29.2
            + 0.15 * deterministic_noise(self.seed, f"{asset}.cws", dt, 300),
            "CW Return Temperature": 33.0
            + 0.18 * deterministic_noise(self.seed, f"{asset}.cwr", dt, 300),
            "CW Flow": max(0.1, cooling / (4.186 * 3.8)),
            "CW Approach": 3.2
            + 0.08 * deterministic_noise(self.seed, f"{asset}.approach", dt, 300),
            "Condenser Pressure": 760
            + 20 * deterministic_noise(self.seed, f"{asset}.cond", dt, 300),
        }

    def _energy_integral(self, asset: str, dt: datetime) -> float:
        """Compatibility integral for domains not yet migrated to authoritative power."""
        hours = (dt - self.epoch).total_seconds() / 3600
        base = self._asset_scalar(asset, 4, 90)
        amplitude = 0.12 * base
        omega = 2 * math.pi / 24
        phase = self._asset_scalar(asset + "|phase", 0, 2 * math.pi)
        integral = base * hours + amplitude / omega * (
            math.cos(phase) - math.cos(omega * hours + phase)
        )
        return max(0, integral)

    @staticmethod
    def _physical_value(value: Any, dtype: str) -> Any:
        if dtype == "Boolean":
            return bool(value)
        if dtype.startswith("Int"):
            return int(round(float(value)))
        if dtype == "String":
            if isinstance(value, bool):
                return "ON" if value else "OFF"
            return str(value)
        if isinstance(value, float):
            return round(value, 4)
        return value

    def _value_for(
        self,
        point: dict,
        dt: datetime,
        site: dict,
        world: PhysicalWorld | None = None,
        constraint_windows: list[ConstraintWindow] | None = None,
    ) -> Any:
        if (
            point.get("sourceClass") in {"STATIC_METADATA", "SUPPORT_CONTROL", "TEST_SUPPORT"}
            and point.get("sourceValue") is not None
        ):
            return point["sourceValue"]

        name = point["memberName"].lower()
        path = point["exportPath"].lower()
        dtype = point["dataType"]
        unit = point.get("engUnit")
        noise = deterministic_noise(self.seed, point["signalKey"], dt)
        asset = (
            point.get("instancePath")
            or point.get("folderPath")
            or point["exportPath"].rsplit("/", 1)[0]
        )
        member = point["memberName"]

        state = world.asset(asset) if world else None
        if state is not None and member in state.metrics:
            return self._physical_value(state.metrics[member], dtype)
        if (
            state is not None
            and point.get("sourceClass") == "ENERGY_INTEGRAL"
            and asset.lower() in self.authoritative_energy_assets
        ):
            energy = self.energy_integrator.energy_kwh(asset, dt, constraint_windows)
            return self._physical_value(energy, dtype)

        if dtype == "Boolean":
            return False
        if dtype.startswith("Int"):
            if any(token in name for token in ("fault", "alarm", "failure", "trip", "trouble")):
                return 0
            if any(
                token in name
                for token in (
                    "on_off",
                    "on off",
                    "running",
                    "run",
                    "status",
                    "comm",
                    "auto_manual",
                    "mode",
                    "link",
                    "admin",
                )
            ):
                return 1
            return int(max(0, round(10 + 2 * noise)))
        if dtype == "String":
            return point.get("sourceValue") or point["instanceName"] or ""

        if point.get("typeId") in {
            "GPM96",
            "GPQM144",
            "GPQM96",
            "GEM230",
            "GEM630",
            "GEM630-CT-L",
            "Production/GDC230",
            "Production/GPQM144 Pro",
            "Production/GPM96",
            "Production/GEM130",
            "Production/GEM630",
            "Production/E820",
            "Production/GEM230",
            "Production/GPQM96",
        }:
            metrics = self._meter_metrics(asset, dt)
            if member in metrics:
                return round(metrics[member], 4)
            if member.startswith("THD"):
                return round(2.2 + 0.8 * noise, 3)
            if member == "Wh_Im":
                return round(self._energy_integral(asset, dt), 3)
        if point.get("typeId") == "BCPM":
            watts = self._asset_scalar(asset, 2.5, 14.0) * (
                1 + 0.12 * math.sin(_day_fraction(dt) * 2 * math.pi)
            )
            if name == "active power":
                return round(watts, 3)
            if name == "current":
                return round(watts * 1000 / (230 * 0.95), 3)
            if "accumulated energy" in name:
                return round(self._energy_integral(asset, dt), 3)
        if point.get("typeId") == "Chiller":
            metrics = self._chiller_metrics(asset, dt, site)
            if member in metrics:
                return round(metrics[member], 4)
        if "supply air temperature" in name or name == "sat":
            return round((14.4 if "crac" in path else 15.2) + 0.25 * noise, 3)
        if "return air temperature" in name or name == "rat":
            return round(24.1 + 0.3 * noise, 3)
        if "humidity" in name or "%rh" == (unit or "").lower():
            return round(52 + 2 * noise, 3)
        if "temperature" in name or unit == "°C":
            if "outside" in name:
                return site["outsideTempC"]
            if "chw supply" in name or "chws" in name:
                return round(5.3 + 0.08 * noise, 3)
            if "chw return" in name or "chwr" in name:
                return round(11.2 + 0.15 * noise, 3)
            if "cw supply" in name or "cws" in name:
                return round(29.2 + 0.2 * noise, 3)
            if "cw return" in name or "cwr" in name:
                return round(33.0 + 0.25 * noise, 3)
            return round(23.2 + 0.6 * noise, 3)
        if "energy" in name or "wh_im" in name or "accumulated" in name:
            return round(self._energy_integral(asset, dt), 3)
        if unit in {"kW", "kVA", "kVAR"} or "power" in name or name in {
            "ptot",
            "p1",
            "p2",
            "p3",
        }:
            base = max(
                0.1,
                site["facilityLoadKw"]
                / max(1, self.manifest["sourceCounts"].get("udtInstances", 831))
                * 4.8,
            )
            if "chiller" in path:
                base = site["plantLoadKw"] / 3
            if "cooling tower" in path:
                base = 18 + 6 * ((site["outsideTempC"] - 25) / 8)
            if "ups" in path:
                base = site["itLoadKw"] / 25
            if unit == "kVA":
                base /= 0.94
            if unit == "kVAR":
                base *= 0.36
            return round(max(0, base * (1 + 0.04 * noise)), 3)
        if unit == "V" or "voltage" in name:
            nominal = (
                400
                if any(token in name for token in ("l1-l2", "l2-l3", "l3-l1", "v12", "v23", "v31"))
                else 230
            )
            return round(nominal * (1 + 0.008 * noise), 3)
        if unit == "A" or "current" in name:
            return round(max(0, 42 * (1 + 0.18 * noise)), 3)
        if unit == "Hz" or "frequency" in name:
            return round(50 + 0.035 * noise, 3)
        if unit in {"%", "%RH"} or any(
            token in name for token in ("load", "speed", "position", "capacity", "utilization", "soc")
        ):
            return round(min(100, max(0, 62 + 8 * noise)), 3)
        if "flow" in name:
            return round(max(0.05, 8.5 * (1 + 0.12 * noise)), 3)
        if "pressure" in name:
            return round(max(0, 180 * (1 + 0.08 * noise)), 3)
        if name == "cop":
            return round(4.44 + 0.03 * noise, 3)
        if "kw per rt" in name:
            return round(3.517 / 4.44, 3)
        if point.get("sourceValue") is not None:
            return point["sourceValue"]
        return round(10 + noise, 3)
