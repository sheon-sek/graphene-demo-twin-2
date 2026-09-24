"""Fire protection and lifts (#26): the life-safety devices the export keeps as loose points.

Fire: each fire zone (a `Fire Zone` Unexported Asset standing in the zone's first room) holds
the smoke in its air and the temperature at its ceiling. A fire in the zone (the
`constraint.fire_kw` it releases) fills it with smoke and heats the ceiling above the room's
air; with no fire the exhaust clears the smoke. Its detectors read those conditions: a smoke
detector alarms on obscuration, a heat detector on its fixed temperature, so a room that
overheats trips its heat detectors with no fire at all. A call point alarms only when someone
presses it (a false alarm). A zone with an alarm valve is sprinklered: once the ceiling
reaches the sprinkler heads' temperature they open, water flows through the valve, the fire
pumps start and the sprinklers knock the fire down to a fraction of its heat. A zone in alarm
shuts down the fresh-air handlers that supply its rooms (their `constraint.fire_alarm` input,
which also sets PAHU `Main Fire Alarm`), and the lifts return to the ground floor and park
with their doors open.

A detector fault puts the device in fault on the panel (its point reads true) but it detects
nothing; a false alarm makes it report a fire that is not there, with the same consequences as
a real one. Neither changes the air.

Lifts: each car runs a simple traffic model, answering calls to random floors, more often in
office hours, travelling a floor every LIFT_FLOOR_S and dwelling with its doors open. A stuck
car stops where it is with its doors shut until the fault is cleared.
"""

import functools
import math

from graphene_demo_twin.plant_design import ConnectionKind, PlantDesign
from graphene_demo_twin.sim.engine import StepContext
from graphene_demo_twin.sim.events import Event
from graphene_demo_twin.sim.state import AssetState, WorldState
from graphene_demo_twin.sim.weather import DAY_S, LOCAL_OFFSET_S, site_air

FIRE_ZONE = "Fire Zone"
SMOKE_DETECTOR = "Smoke Detector"
HEAT_DETECTOR = "Heat Detector"
CALL_POINT = "Manual Call Point"
ALARM_VALVE = "Alarm Valve"
FIRE_PUMP = "Fire Pump"
LIFT = "Lift"
FIRE_DEVICES = (SMOKE_DETECTOR, HEAT_DETECTOR, CALL_POINT, ALARM_VALVE, FIRE_PUMP)

SMOKE_PER_KWS = 4e-4
"""Obscuration a fire adds to its zone's air, in %/m per kW per second."""
SMOKE_TAU_S = 300.0
"""Time constant of the smoke exhaust."""
SMOKE_ALARM = 2.5
"""Obscuration at which a smoke detector alarms, in %/m; it resets below SMOKE_RESET."""
SMOKE_RESET = 1.5
PLUME_K_PER_KW = 0.2
"""How far a fire's plume lifts the ceiling above the room's air, per kW."""
PLUME_TAU_S = 60.0
HEAT_ALARM_C = 57.0
"""Fixed temperature of a heat detector; it resets below HEAT_RESET_C."""
HEAT_RESET_C = 50.0
SPRINKLER_C = 68.0
"""Temperature at which a sprinkler head's bulb bursts."""
SPRINKLED = 0.3
"""Share of a fire's heat left once sprinklers flow on it."""
UNCONDITIONED_RISE_C = 2.0
"""How far an indoor room with no air units sits above the outdoor air."""

LIFT_FLOOR_S = 5
"""Travel time per floor."""
LIFT_START_S = 3
"""Doors closing and the car accelerating, before it passes the first floor."""
LIFT_DWELL_S = 8
"""Doors open at a landing."""
LIFT_CALL = {True: 0.02, False: 0.004}
"""Chance per second an idle car gets a call, in and out of office hours."""
DOOR_OPEN, DOOR_CLOSED = 0, 1
"""Lift `Door Status` codes (the export's cars are moving with Door Status 1)."""


class FireDomain:
    """Smoke and heat in every fire zone, its devices' states and the zone alarms; see the
    module docstring. It steps before the air units, so a zone alarm stops its fresh-air
    handlers in the same step, and before the thermal zones, which take in its fire's heat."""

    settling_s = int(8 * SMOKE_TAU_S)

    def initial(self, ctx: StepContext) -> dict[str, AssetState]:
        states: dict[str, AssetState] = {}
        for zone in fire_zones(ctx.design):
            states[zone.id] = {
                "fire_kw": 0.0,
                "smoke_pct_m": 0.0,
                "ceiling_c": 0.0,
                "sprinkler": False,
                "alarm": False,
                "shutdown": False,
            }
            for device in zone.devices:
                states[device] = {"detecting": False, "fault": False, "alarm": False, "on": False}
        for pump in fire_pumps(ctx.design):
            states[pump] = {"detecting": False, "fault": False, "alarm": False, "on": False}
        return states

    def complete(self, state: WorldState, ctx: StepContext) -> None:
        for zone in fire_zones(ctx.design):
            state.assets[zone.id]["ceiling_c"] = room_air_c(state, ctx.design, zone.room)
        self._devices(state, ctx.design)

    def handles(self, event: Event, design: PlantDesign) -> bool:
        return False

    def apply(self, event: Event, state: WorldState) -> None:
        raise AssertionError("no events")

    def step(self, state: WorldState, ctx: StepContext) -> None:
        a = state.assets
        dt = ctx.dt
        for zone in fire_zones(ctx.design):
            s = a[zone.id]
            fire = s.get("constraint.fire_kw", 0.0)
            ceiling = s["ceiling_c"]
            if zone.sprinklered:
                if ceiling >= SPRINKLER_C:
                    s["sprinkler"] = True
                elif fire == 0.0 and ceiling < HEAT_RESET_C:
                    s["sprinkler"] = False  # the fire is out and the valve is shut again
            heat = fire * (SPRINKLED if s["sprinkler"] else 1.0)
            s["fire_kw"] = heat
            smoke = s["smoke_pct_m"]
            smoke += (SMOKE_PER_KWS * heat - smoke / SMOKE_TAU_S) * dt
            s["smoke_pct_m"] = smoke if smoke > 1e-9 else 0.0
            air = room_air_c(state, ctx.design, zone.room)
            rise = ceiling - air
            rise += (PLUME_K_PER_KW * heat - rise) * dt / PLUME_TAU_S
            s["ceiling_c"] = air + rise
        self._devices(state, ctx.design)

    def _devices(self, state: WorldState, design: PlantDesign) -> None:
        a = state.assets
        flowing = False
        for zone in fire_zones(design):
            z = a[zone.id]
            smoke, ceiling = z["smoke_pct_m"], z["ceiling_c"]
            alarm = False
            for device, kind in zip(zone.devices, zone.kinds, strict=True):
                d = a[device]
                fault = d.get("constraint.detector_fault", 0.0) >= 0.5
                false_alarm = d.get("observation.false_alarm", 0.0) >= 0.5
                was = d["detecting"]
                if fault:
                    detecting = False
                elif kind == SMOKE_DETECTOR:
                    detecting = smoke >= (SMOKE_RESET if was else SMOKE_ALARM)
                elif kind == HEAT_DETECTOR:
                    detecting = ceiling >= (HEAT_RESET_C if was else HEAT_ALARM_C)
                elif kind == ALARM_VALVE:
                    detecting = z["sprinkler"]
                else:
                    detecting = False
                d["detecting"], d["fault"] = detecting, fault
                d["alarm"] = detecting or false_alarm
                d["on"] = d["alarm"] or fault
                if kind in (SMOKE_DETECTOR, HEAT_DETECTOR, CALL_POINT, ALARM_VALVE):
                    alarm = alarm or d["alarm"]
                if kind == ALARM_VALVE and detecting:
                    flowing = True
            z["alarm"] = alarm
            # The fire panel's interlock: a zone in alarm stops the fresh-air handlers that
            # supply it. It releases only the stop it gave.
            if alarm:
                for pahu in zone.fresh_air:
                    a[pahu]["constraint.fire_alarm"] = 1.0
            elif z["shutdown"]:
                for pahu in zone.fresh_air:
                    a[pahu].pop("constraint.fire_alarm", None)
            z["shutdown"] = alarm
        for pump in fire_pumps(design):
            p = a[pump]
            fault = p.get("constraint.detector_fault", 0.0) >= 0.5
            p["detecting"] = flowing and not fault
            p["fault"] = fault
            p["alarm"] = p["detecting"]
            p["on"] = p["detecting"] or fault


def fire_alarm(state: WorldState, design: PlantDesign) -> bool:
    """Whether any fire zone is in alarm."""
    return any(state.assets[z.id]["alarm"] for z in fire_zones(design))


def room_air_c(state: WorldState, design: PlantDesign, room: str) -> float:
    """The air in `room`: its thermal zone's, or else the outdoor air (a little warmer indoors)."""
    zone = state.assets.get(room)
    if zone is not None and "temp_c" in zone:
        return zone["temp_c"]
    outdoor = site_air(state, design)["dry_bulb_c"]
    return outdoor if design.room(room).outdoor else outdoor + UNCONDITIONED_RISE_C


class _FireZone:
    __slots__ = ("devices", "fresh_air", "id", "kinds", "room", "sprinklered")

    def __init__(
        self,
        zone: str,
        room: str,
        devices: tuple[str, ...],
        kinds: tuple[str, ...],
        fresh_air: tuple[str, ...],
    ) -> None:
        self.id, self.room, self.devices, self.kinds = zone, room, devices, kinds
        self.fresh_air = fresh_air
        self.sprinklered = ALARM_VALVE in kinds


@functools.cache
def fire_zones(design: PlantDesign) -> tuple[_FireZone, ...]:
    """Every fire zone: the room it stands in, its devices (fire pumps apart) and the
    fresh-air handlers supplying any room in the zone."""
    zones = []
    for z in design.unexported.values():
        if z.type_id != FIRE_ZONE:
            continue
        room = design.asset(z.id).room
        folder = z.observed_by[0]
        devices = tuple(
            u.id
            for u in design.unexported.values()
            if u.type_id in FIRE_DEVICES and u.type_id != FIRE_PUMP and u.observed_by[0] == folder
        )
        floor, fire_zone = design.room(room).floor, design.room(room).fire_zone
        fresh_air = tuple(
            a.path
            for a in design.assets.values()
            if a.type_id == "PAHU"
            and any(
                design.is_room(r)
                and design.room(r).floor == floor
                and design.room(r).fire_zone == fire_zone
                for r in design.downstream(a.path, ConnectionKind.AIR)
            )
        )
        kinds = tuple(design.asset(d).type_id for d in devices)
        zones.append(_FireZone(z.id, room, devices, kinds, fresh_air))
    return tuple(zones)


@functools.cache
def fire_pumps(design: PlantDesign) -> tuple[str, ...]:
    return tuple(u.id for u in design.unexported.values() if u.type_id == FIRE_PUMP)


@functools.cache
def lifts(design: PlantDesign) -> tuple[str, ...]:
    return tuple(u.id for u in design.unexported.values() if u.type_id == LIFT)


def office_hours(time: int) -> bool:
    local = time + LOCAL_OFFSET_S
    hour = (local % DAY_S) / 3600.0
    return (local // DAY_S + 3) % 7 < 5 and 8.0 <= hour < 18.0


class LiftDomain:
    """Lifts 1–3: level, direction and doors under a simple traffic model; see the module
    docstring. Levels count the floors served from 1 (Ground) upwards. It steps after the
    fire domain, whose zone alarms recall the cars."""

    settling_s = 0

    def initial(self, ctx: StepContext) -> dict[str, AssetState]:
        return {
            lift: {
                "level": 1,
                "target": 1,
                "from_level": 1,
                "phase": "idle",
                "since": ctx.time,
                "until": ctx.time,
                "moving_until": ctx.time,
                "direction": "Idle",
                "door": DOOR_CLOSED,
            }
            for lift in lifts(ctx.design)
        }

    def handles(self, event: Event, design: PlantDesign) -> bool:
        return False

    def apply(self, event: Event, state: WorldState) -> None:
        raise AssertionError("no events")

    def step(self, state: WorldState, ctx: StepContext) -> None:
        now = ctx.time + int(ctx.dt)
        top = len(ctx.design.floors)
        recall = fire_alarm(state, ctx.design)
        busy = office_hours(ctx.time)
        for lift in lifts(ctx.design):
            s = state.assets[lift]
            if s.get("constraint.stuck", 0.0) >= 0.5:
                s["door"] = DOOR_CLOSED
                s["stuck"] = True
                continue
            if s.pop("stuck", False) and s["phase"] == "moving":
                # Released between floors: carry on to the landing it was heading for.
                self._travel(s, s["level"], s["target"], ctx.time)
            phase = s["phase"]
            if phase == "moving":
                if now >= s["until"]:
                    s["level"] = s["target"]
                    self._stop(s, now)
                else:
                    passed = max(now - s["since"] - LIFT_START_S, 0) // LIFT_FLOOR_S
                    step = 1 if s["target"] > s["from_level"] else -1
                    s["level"] = s["from_level"] + step * min(
                        passed, abs(s["target"] - s["from_level"])
                    )
                continue
            if recall:
                if s["level"] != 1:
                    self._travel(s, s["level"], 1, ctx.time)
                else:  # parked at the ground floor with its doors open
                    s["phase"], s["door"], s["direction"] = "doors", DOOR_OPEN, "Idle"
                    s["until"] = now + LIFT_DWELL_S
                continue
            if phase == "doors":
                if now >= s["until"]:
                    s["phase"], s["door"] = "idle", DOOR_CLOSED
                continue
            if ctx.uniform(f"lift.call.{lift}") < LIFT_CALL[busy]:
                floor = 1 + math.floor(ctx.uniform(f"lift.floor.{lift}") * (top - 1))
                target = floor + 1 if floor >= s["level"] else floor
                self._travel(s, s["level"], target, ctx.time)

    @staticmethod
    def _travel(s: AssetState, level: int, target: int, time: int) -> None:
        s["from_level"], s["target"], s["phase"], s["since"] = level, target, "moving", time
        s["until"] = s["moving_until"] = time + LIFT_START_S + LIFT_FLOOR_S * abs(target - level)
        s["direction"] = "Up" if target > level else "Down"
        s["door"] = DOOR_CLOSED

    @staticmethod
    def _stop(s: AssetState, now: int) -> None:
        s["phase"], s["door"], s["direction"] = "doors", DOOR_OPEN, "Idle"
        s["since"], s["until"] = now, now + LIFT_DWELL_S
