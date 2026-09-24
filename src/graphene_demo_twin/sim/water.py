"""The water network (Plant Design review item A14) and leak detection.

Municipal supply enters through G_V1 and fills the two ground tanks through their inlet
valves, which float between 85 % and 95 %. Three transfer pumps (TP1 lead, TP2 lag, TP3
standby) lift water up the riser to the two roof tanks under level control on the roof tanks'
readings: the lead starts below 60 %, the lag below 40 %, and both stop at 90 %; the standby
takes the place of a duty pump that cannot run. Each roof tank's inlet valve closes near full.
A booster (BP1 duty, BP2 standby) pressurises the makeup headers and the domestic branch from
the roof tanks, and one VSD makeup pump per tower cell holds its discharge pressure at
setpoint, feeding its cell's basin through a float valve. The two AC makeup pumps top up the
closed chilled-water loop from the ground tank branch, holding its static pressure.

Each tower cell loses water with the heat its group rejects: evaporation (the heat over the
latent heat of water), drift (a share of the condenser water circulating), and blowdown (the
evaporation over the cycles of concentration less one, while condenser water flows). Its basin
level integrates makeup less losses; at the low-level trip the cell reports it (`basin_low`)
and the plant stops its fan (`sim.plant`), so the chiller it serves lifts harder. WUE is the
towers' water use over IT power (`sim.site`).

Every tank level, basin level and pool of leaked water is integrated: a transfer pump failure
drains the roof tanks at what the towers and the domestic branch use, and only once they run
dry does makeup pressure collapse and the basins fall.

A leak cable reports a leak only while water lies on it: a pipe leak (the `constraint.leak_lps`
a fault drives on the cable, standing for pipework in the room it runs under) pours water onto
the floor at one position along the cable, the floor drains it away with a time constant, and
the cable alarms and reports the position while the pool is deep enough to bridge it. The water
comes from the closed CHW loop, or in a water plant room from that floor's cold-water tanks.

Physical Constraints read here: `constraint.trip` on transfer, booster, makeup and AC makeup
pumps, `constraint.supply_loss` on the municipal inlet G_V1, `constraint.stuck` on any valve
(it holds its position), `constraint.leak_lps` on a leak cable, and the observation
`observation.level_offset_pct` on a roof tank's level sensor.
"""

import functools
import zlib
from dataclasses import dataclass

from graphene_demo_twin.plant_design import ConnectionKind, PlantDesign
from graphene_demo_twin.sim.electrical import network
from graphene_demo_twin.sim.engine import StepContext
from graphene_demo_twin.sim.events import Event
from graphene_demo_twin.sim.plant import PLANT, plant_layout
from graphene_demo_twin.sim.state import AssetState, WorldState

__all__ = ["WATER", "WaterDomain", "loss_keys", "water_layout", "water_nodes"]

WATER = "water"
"""World-state key of the network as a whole: flows between its parts and the CHW loop's
static pressure. Not a Plant Design node."""

CW = "Cold Water and Sanitary System"
TRANSFER_TYPE = "CW Transfer Pump"
BOOSTER_TYPE = "CW Booster Pump"
MAKEUP_TYPE = "Makeup Water Pump"
AC_MAKEUP_TYPE = "AC Makeup Pump"
CABLE_TYPE = "Water Leak Cable Sensor"
VALVE_TYPES = ("CW Ground Valve", "CW Roof Valve")

GROUND_L, ROOF_L = 200_000.0, 60_000.0
"""Tank capacities (Plant Design basis: 2 × 200 m³ ground, 2 × 60 m³ roof)."""
GROUND_START_PCT, ROOF_START_PCT = 90.0, 75.0
GROUND_OPEN_PCT, GROUND_CLOSE_PCT = 85.0, 95.0
ROOF_OPEN_PCT, ROOF_CLOSE_PCT = 93.0, 97.0
LEAD_START_PCT, LAG_START_PCT, TRANSFER_STOP_PCT = 60.0, 40.0, 90.0
TANK_LOW_PCT, TANK_HIGH_PCT = 20.0, 97.0
"""Readings at which a tank's High_Low Water Level Alarm sounds."""
SUCTION_L = 300.0
"""Water below which a pump drawing from a tank set loses suction, in proportion."""
MUNICIPAL_LPS = 6.0
"""Flow through each open ground tank inlet with the mains on."""
TRANSFER_LPS = 4.0
DOMESTIC_LPS = 0.2
"""Sanitary and domestic draw on the booster header."""

BOOSTER_KPA, STATIC_KPA = 150.0, 20.0
"""Makeup header pressure with a booster running, and from the roof tanks' head alone."""
MAKEUP_SP_KPA, MAKEUP_HEAD_KPA = 300.0, 250.0
"""A makeup pump's discharge setpoint and its head at full speed."""
MAKEUP_MIN_PCT, MAKEUP_PCT_PER_S = 30.0, 5.0
MAKEUP_LPS = 1.5
"""A makeup pump's flow into its basin at setpoint pressure with the float valve open."""
MAKEUP_KW, MAKEUP_VOLTS, MAKEUP_PF = 0.9, 400.0, 0.85

BASIN_L = 3000.0
"""Water in a cell basin at 100 %."""
BASIN_SP_PCT, FLOAT_BAND_PCT = 70.0, 5.0
"""Where a basin's float valve closes, and how far below it it is fully open."""
LOW_TRIP_PCT, LOW_RESET_PCT = 30.0, 35.0
LATENT_KJ_PER_KG = 2430.0
CYCLES_OF_CONCENTRATION = 4.0
DRIFT = 2e-5
"""Drift as a share of the condenser water circulating."""

LOOP_START_KPA = 190.0
LOOP_KPA_PER_L = 0.1
"""Static pressure the closed CHW loop loses per litre of water, through its expansion vessel."""
LOOP_WEEP_LPS = 0.002
AC_MAKEUP_LPS = 0.5
AC_LEAD = (150.0, 200.0)
AC_ASSIST = (120.0, 180.0)
"""(start below, stop at) the loop pressure, lead and assist pump."""
LOOP_RELIEF_KPA = 250.0

TRANSFER_KW, BOOSTER_KW, AC_MAKEUP_KW = 3.0, 2.2, 0.75

CABLE_M = 20.0
"""Length of every leak cable (the export's Cable Length)."""
DETECT_L = 2.0
"""Water on the floor that bridges a leak cable."""
DRAIN_TAU_S = 600.0


@dataclass(frozen=True, slots=True)
class WaterLayout:
    ground_tanks: tuple[str, ...]
    ground_inlets: tuple[str, ...]
    """Inlet valve of each ground tank, in order."""
    municipal: str
    transfer: tuple[str, ...]
    """Lead, lag, standby."""
    transfer_valves: tuple[str, ...]
    """Discharge valve of each transfer pump."""
    riser: str
    roof_tanks: tuple[str, ...]
    roof_inlets: tuple[str, ...]
    roof_outlets: tuple[str, ...]
    boosters: tuple[str, ...]
    """Duty, standby."""
    ac_branch: str
    ac_makeup: tuple[str, ...]
    """Lead, assist."""
    makeup: dict[str, str]
    """Makeup pump → the tower cell it feeds."""
    headers: dict[str, str]
    """Makeup pump → the makeup header valve it draws through."""
    header_valves: tuple[str, ...]
    valves: tuple[str, ...]
    cables: tuple[str, ...]
    cable_source: dict[str, str]
    """Leak cable → where water leaking in its room comes from: "loop", "ground" or "roof"."""

    @property
    def pumps(self) -> tuple[str, ...]:
        return (*self.transfer, *self.boosters, *self.ac_makeup, *self.makeup)

    @property
    def nodes(self) -> tuple[str, ...]:
        return (*self.ground_tanks, *self.roof_tanks, *self.pumps, *self.valves, *self.cables)


@functools.cache
def water_layout(design: PlantDesign) -> WaterLayout:
    def of(type_id: str) -> list[str]:
        return sorted(a.path for a in design.assets.values() if a.type_id == type_id and a.room)

    water = ConnectionKind.WATER
    makeup = {p: design.downstream(p, water)[0] for p in of(MAKEUP_TYPE)}
    headers = {p: design.upstream(p, water)[0] for p in makeup}
    cable_source = {}
    for cable in of(CABLE_TYPE):
        room = design.room(design.asset(cable).room)
        if room.kind != "water":
            cable_source[cable] = "loop"
        else:
            cable_source[cable] = "ground" if room.floor == "Ground" else "roof"
    n = lambda *names: tuple(f"{CW}/{x}" for x in names)  # noqa: E731
    return WaterLayout(
        ground_tanks=n("G_T1", "G_T2"),
        ground_inlets=n("G_V2", "G_V3"),
        municipal=f"{CW}/G_V1",
        transfer=n("G_TP1", "G_TP2", "G_TP3"),
        transfer_valves=n("G_V6", "G_V7", "G_V8"),
        riser=f"{CW}/G_V9",
        roof_tanks=n("R_T1", "R_T2"),
        roof_inlets=n("R_V1", "R_V2"),
        roof_outlets=n("R_V3", "R_V4"),
        boosters=n("R_BP1", "R_BP2"),
        ac_branch=f"{CW}/G_V10",
        ac_makeup=("AC Makeup Tank/G_P1", "AC Makeup Tank/G_P2"),
        makeup=makeup,
        headers=headers,
        header_valves=tuple(sorted(set(headers.values()))),
        valves=tuple(sorted(p for t in VALVE_TYPES for p in of(t))),
        cables=tuple(of(CABLE_TYPE)),
        cable_source=cable_source,
    )


@functools.cache
def water_nodes(design: PlantDesign) -> tuple[str, ...]:
    """The pumps whose power the water domain works out."""
    return water_layout(design).pumps


@functools.cache
def _supply_flags(design: PlantDesign) -> tuple[tuple[str, str | None], ...]:
    """Each pump → the node whose `live` says whether it has supply (`sim.electrical`)."""
    flags = network(design).supply_flag
    return tuple((p, flags.get(p)) for p in water_layout(design).pumps)


@functools.cache
def _groups(design: PlantDesign) -> tuple:
    """Each Tower Group: its name, its condenser-water pump, the WATER variables of what each of
    its cells loses (`loss_keys`), and each cell with the makeup pump feeding it and the header
    valve that pump draws through."""
    layout = water_layout(design)
    pump_of = {cell: pump for pump, cell in layout.makeup.items()}
    return tuple(
        (
            leg.tower,
            leg.cw_pump,
            loss_keys(leg.tower),
            tuple((c, pump_of[c], layout.headers[pump_of[c]]) for c in leg.cells),
        )
        for leg in plant_layout(design).legs
    )


_LIVE: AssetState = {}


def loss_keys(tower: str) -> tuple[str, str, str]:
    """WATER variables of the evaporation, drift and blowdown of each cell of `tower`, in L/s."""
    return (f"{tower}.evap_lps", f"{tower}.drift_lps", f"{tower}.blowdown_lps")


def leak_position_m(cable: str) -> float:
    """Where along `cable` a leak in its room reaches it: fixed by the pipework's route."""
    return round(CABLE_M * (0.15 + 0.7 * (zlib.crc32(cable.encode()) % 1000) / 1000.0), 1)


NORMALLY_CLOSED = frozenset({f"{CW}/R_V9"})
"""The header drain / bypass."""


class WaterDomain:
    """Steps after the chiller plant, on the heat its towers rejected this step, and before the
    site loads and the electrical network, which take up its pumps' power. The plant reads the
    basins' low-level trips one step behind."""

    settling_s = int(6 * DRAIN_TAU_S)
    """Leaked water drains below what a cable senses; tank and basin levels are stores, and
    remember what a fault cost as a battery's charge or a fuel tank does."""

    def initial(self, ctx: StepContext) -> dict[str, AssetState]:
        design = ctx.design
        layout = water_layout(design)
        states: dict[str, AssetState] = {
            WATER: {
                "loop_kpa": LOOP_START_KPA,
                "municipal_lps": 0.0,
                "transfer_lps": 0.0,
                "header_kpa": BOOSTER_KPA,
                "tower_makeup_lps": 0.0,
                "tower_loss_lps": 0.0,
                "domestic_lps": 0.0,
                "ac_makeup_lps": 0.0,
                "transfer_n": 0,
            }
        }
        for tank, start, cap in (
            *((t, GROUND_START_PCT, GROUND_L) for t in layout.ground_tanks),
            *((t, ROOF_START_PCT, ROOF_L) for t in layout.roof_tanks),
        ):
            states[tank] = {
                "capacity_l": cap,
                "volume_l": cap * start / 100.0,
                "level_pct": start,
                "sensed_pct": start,
                "alarm": False,
            }
        for pump in layout.pumps:
            states[pump] = {"running": False, "trip": False, "power_kw": 0.0, "powered": True}
            states[pump]["has_alarm"] = False
        for pump in layout.makeup:
            states[pump] |= {
                "speed_pct": 0.0,
                "discharge_kpa": 0.0,
                "sp_kpa": MAKEUP_SP_KPA,
                "flow_lps": 0.0,
                "run_s": 0.0,
            }
        for valve in layout.valves:
            is_open = valve not in NORMALLY_CLOSED and valve not in layout.ground_inlets
            states[valve] = {"cmd_open": is_open, "open": is_open}
        for cell in layout.makeup.values():
            states[cell] = {
                "basin_pct": BASIN_SP_PCT,
                "basin_sp_pct": BASIN_SP_PCT,
                "basin_low": False,
            }
        for cable in layout.cables:
            states[cable] = {"water_l": 0.0, "status": 0, "position_m": 0.0}
        states[PLANT] = {
            f"{leg.tower}.level_pct": BASIN_SP_PCT for leg in plant_layout(design).legs
        }
        return states

    def complete(self, state: WorldState, ctx: StepContext) -> None:
        """Every basin where its float valve balances its losses, and every flow and pressure
        at what the tanks as they start give."""
        self._step(state, ctx, settle=True)

    def handles(self, event: Event, design: PlantDesign) -> bool:
        return False

    def apply(self, event: Event, state: WorldState) -> None:
        raise AssertionError("no events")

    def step(self, state: WorldState, ctx: StepContext) -> None:
        self._step(state, ctx, settle=False)

    def _step(self, state: WorldState, ctx: StepContext, settle: bool) -> None:
        design, a, dt = ctx.design, state.assets, ctx.dt
        layout = water_layout(design)
        w = a[WATER]

        # Valves answer their commands unless stuck; the tanks' readings.
        for valve in layout.valves:
            v = a[valve]
            if v.get("constraint.stuck", 0.0) < 0.5:
                v["open"] = v["cmd_open"]
        for tank in (*layout.ground_tanks, *layout.roof_tanks):
            t = a[tank]
            t["level_pct"] = 100.0 * t["volume_l"] / t["capacity_l"]
            sensed = t["level_pct"] + t.get("observation.level_offset_pct", 0.0)
            t["sensed_pct"] = min(max(sensed, 0.0), 100.0)
            t["alarm"] = not TANK_LOW_PCT <= t["sensed_pct"] <= TANK_HIGH_PCT

        # Pumps: supply and trips.
        for pump, flag in _supply_flags(design):
            s = a[pump]
            s["powered"] = flag is None or a.get(flag, _LIVE).get("live", True)
            s["trip"] = s.get("constraint.trip", 0.0) >= 0.5

        def available(pump: str) -> bool:
            return a[pump]["powered"] and not a[pump]["trip"]

        # Ground tanks: the mains through each open inlet, floating on level.
        muni = a[layout.municipal]
        mains = 1.0 - min(muni.get("constraint.supply_loss", 0.0), 1.0) if muni["open"] else 0.0
        inflow = []
        for tank, inlet in zip(layout.ground_tanks, layout.ground_inlets, strict=True):
            level, v = a[tank]["level_pct"], a[inlet]
            if level < GROUND_OPEN_PCT:
                v["cmd_open"] = True
            elif level >= GROUND_CLOSE_PCT:
                v["cmd_open"] = False
            inflow.append(MUNICIPAL_LPS * mains if v["open"] else 0.0)
        w["municipal_lps"] = sum(inflow)
        ground_l = sum(a[t]["volume_l"] for t in layout.ground_tanks)
        ground_avail = min(ground_l / SUCTION_L, 1.0)

        # Transfer pumps on the roof tanks' readings: lead, lag, and the standby for either.
        sensed = sum(a[t]["sensed_pct"] for t in layout.roof_tanks) / len(layout.roof_tanks)
        n = w["transfer_n"]
        if sensed >= TRANSFER_STOP_PCT:
            n = 0
        elif sensed < LAG_START_PCT:
            n = 2
        elif sensed < LEAD_START_PCT:
            n = max(n, 1)
        w["transfer_n"] = n
        chosen = [p for p in layout.transfer if available(p)][:n]
        for pump, valve in zip(layout.transfer, layout.transfer_valves, strict=True):
            a[pump]["running"] = pump in chosen
            a[valve]["cmd_open"] = pump in chosen
        for tank, inlet in zip(layout.roof_tanks, layout.roof_inlets, strict=True):
            level, v = a[tank]["level_pct"], a[inlet]
            if level >= ROOF_CLOSE_PCT:
                v["cmd_open"] = False
            elif level < ROOF_OPEN_PCT:
                v["cmd_open"] = True
        roof_in = [
            i for t, i in zip(layout.roof_tanks, layout.roof_inlets, strict=True) if a[i]["open"]
        ]
        pumping = sum(
            1
            for p, v in zip(layout.transfer, layout.transfer_valves, strict=True)
            if a[p]["running"] and a[v]["open"]
        )
        path = a[layout.riser]["open"] and roof_in
        transfer = TRANSFER_LPS * pumping * ground_avail if path else 0.0
        w["transfer_lps"] = transfer

        # Boosters and the makeup header, drawing on the roof tanks through their outlets.
        feeding = [
            t for t, o in zip(layout.roof_tanks, layout.roof_outlets, strict=True) if a[o]["open"]
        ]
        roof_l = sum(a[t]["volume_l"] for t in feeding)
        roof_avail = min(roof_l / SUCTION_L, 1.0)
        booster = next((b for b in layout.boosters if available(b)), None)
        for b in layout.boosters:
            a[b]["running"] = b == booster
        header = (BOOSTER_KPA if booster else STATIC_KPA) * roof_avail
        w["header_kpa"] = header
        domestic = DOMESTIC_LPS * roof_avail if a[f"{CW}/R_V8"]["open"] else 0.0

        # Tower cells: what each loses with the heat its group rejects, and its makeup pump
        # holding its discharge pressure as its header allows, into the cell's float valve.
        p = a[PLANT]
        loss_total = makeup_total = 0.0
        headers = {}
        for h in layout.header_valves:
            # What a running pump on this header settles to: speed, pressure, power, alarm.
            supply = roof_avail if a[h]["open"] else 0.0
            need = (MAKEUP_SP_KPA - header) / (
                MAKEUP_HEAD_KPA * (supply if supply > 0.01 else 0.01)
            )
            target = 100.0 * (need if need < 1.0 else 1.0) ** 0.5 if need > 0.0 else 0.0
            target = target if target > MAKEUP_MIN_PCT else MAKEUP_MIN_PCT
            speed = target / 100.0
            discharge = header + supply * MAKEUP_HEAD_KPA * speed * speed
            pressure = (discharge / MAKEUP_SP_KPA if discharge < MAKEUP_SP_KPA else 1.0) ** 0.5
            headers[h] = (supply, target, discharge, pressure, MAKEUP_KW * speed**3)
        step = MAKEUP_PCT_PER_S * dt
        for tower, cw_pump, keys, cells in _groups(design):
            rejected = p[tower + ".rejected_kw"]
            cw_lps = a[cw_pump]["flow_lps"]
            evap = rejected / LATENT_KJ_PER_KG / len(cells)
            drift = DRIFT * cw_lps / len(cells)
            blowdown = evap / (CYCLES_OF_CONCENTRATION - 1.0) if cw_lps > 0.0 else 0.0
            w[keys[0]], w[keys[1]], w[keys[2]] = evap, drift, blowdown
            loss = evap + drift + blowdown
            loss_total += loss * len(cells)
            level_sum = 0.0
            for cell, pump, header_valve in cells:
                c, s = a[cell], a[pump]
                if settle:
                    c["basin_pct"] = c["basin_sp_pct"] - FLOAT_BAND_PCT * loss / MAKEUP_LPS
                basin = c["basin_pct"]
                opening = (c["basin_sp_pct"] - basin) / FLOAT_BAND_PCT
                opening = 0.0 if opening < 0.0 else 1.0 if opening > 1.0 else opening
                on = s["powered"] and not s["trip"]
                supply, target, discharge, pressure, kw = headers[header_valve]
                if on and s["speed_pct"] == target and s["discharge_kpa"] == discharge:
                    flow = MAKEUP_LPS * opening * pressure  # at target, as it stood
                elif on:
                    speed_pct = s["speed_pct"]
                    if settle:
                        speed_pct = target
                    else:
                        change = target - speed_pct
                        speed_pct += step if change > step else -step if change < -step else change
                    speed = speed_pct / 100.0
                    discharge = header + supply * MAKEUP_HEAD_KPA * speed * speed
                    pressure = discharge / MAKEUP_SP_KPA if discharge < MAKEUP_SP_KPA else 1.0
                    flow = MAKEUP_LPS * opening * pressure**0.5
                    s["speed_pct"], s["discharge_kpa"] = speed_pct, discharge
                    s["power_kw"] = MAKEUP_KW * speed * speed * speed
                    s["has_alarm"] = discharge < 0.5 * MAKEUP_SP_KPA
                    s["running"] = True
                else:
                    s["speed_pct"] = s["power_kw"] = s["discharge_kpa"] = flow = 0.0
                    s["has_alarm"] = s["trip"]
                    s["running"] = False
                s["flow_lps"] = flow
                makeup_total += flow
                if not settle:
                    if on:
                        s["run_s"] += dt
                    basin += (flow - loss) * dt * 100.0 / BASIN_L
                    c["basin_pct"] = basin = basin if basin > 0.0 else 0.0
                if basin <= LOW_TRIP_PCT:
                    c["basin_low"] = True
                elif basin >= LOW_RESET_PCT:
                    c["basin_low"] = False
                level_sum += basin
            p[tower + ".level_pct"] = level_sum / len(cells)
        w["tower_loss_lps"] = loss_total
        w["tower_makeup_lps"] = makeup_total
        w["domestic_lps"] = domestic

        # Leaks: water pours onto the floor under the cable, and drains away.
        leaks = {"loop": 0.0, "ground": 0.0, "roof": 0.0}
        for cable in layout.cables:
            s = a[cable]
            leak = s.get("constraint.leak_lps", 0.0)
            if not leak and not s["water_l"]:
                continue  # dry, as it stood
            leaks[layout.cable_source[cable]] += leak
            if not settle:
                s["water_l"] = max(s["water_l"] + (leak - s["water_l"] / DRAIN_TAU_S) * dt, 0.0)
                if s["water_l"] < 1e-3 and not leak:
                    s["water_l"] = 0.0
            wet = s["water_l"] >= DETECT_L
            s["status"] = 1 if wet else 0
            s["position_m"] = leak_position_m(cable) if wet else 0.0

        # AC makeup pumps hold the closed CHW loop's pressure from the ground tank branch.
        loop = w["loop_kpa"]
        ac_flow = 0.0
        for pump, (start, stop) in zip(layout.ac_makeup, (AC_LEAD, AC_ASSIST), strict=True):
            s = a[pump]
            if not available(pump) or loop >= stop:
                s["running"] = False
            elif loop < start:
                s["running"] = True
            s["power_kw"] = AC_MAKEUP_KW if s["running"] else 0.0
            s["has_alarm"] = s["trip"]
            if s["running"] and a[layout.ac_branch]["open"]:
                ac_flow += AC_MAKEUP_LPS * ground_avail
        w["ac_makeup_lps"] = ac_flow
        for pump, kw in (
            *((x, TRANSFER_KW) for x in layout.transfer),
            *((x, BOOSTER_KW) for x in layout.boosters),
        ):
            s = a[pump]
            s["power_kw"] = kw if s["running"] else 0.0
            s["has_alarm"] = s["trip"]

        if settle:
            return
        w["loop_kpa"] = min(
            max(loop + (ac_flow - leaks["loop"] - LOOP_WEEP_LPS) * LOOP_KPA_PER_L * dt, 0.0),
            LOOP_RELIEF_KPA,
        )

        # The tanks integrate what flows in and out.
        ground_out = transfer + ac_flow + leaks["ground"]
        _share(a, layout.ground_tanks, inflow, ground_out, dt)
        roof_out = makeup_total + domestic + leaks["roof"]
        roof_inflow = [transfer / len(roof_in) if i in roof_in else 0.0 for i in layout.roof_inlets]
        draw = [t in feeding for t in layout.roof_tanks]
        _share(a, layout.roof_tanks, roof_inflow, roof_out, dt, draw)


def _share(
    a: dict, tanks: tuple[str, ...], inflow: list[float], out: float, dt: float, draw=None
) -> None:
    """Fill each tank by its inflow and draw `out` from those drawing, by what each holds."""
    drawing = [t for i, t in enumerate(tanks) if draw is None or draw[i]]
    held = sum(a[t]["volume_l"] for t in drawing)
    for tank, q_in in zip(tanks, inflow, strict=True):
        t = a[tank]
        q_out = out * t["volume_l"] / held if tank in drawing and held > 0.0 else 0.0
        t["volume_l"] = min(max(t["volume_l"] + (q_in - q_out) * dt, 0.0), t["capacity_l"])
