"""Measure the non-functional targets (#28) and print them as a Markdown table.

    .venv/bin/python tools/benchmark.py [--quick]

This measures and does not tune. The numbers are recorded in docs/performance.md, and any
optimization is deferred to a follow-up issue. It measures:

- Live World step: one fixed 1 s step of every domain, from steady state and during the
  Golden Demo's incident.
- 1 Hz publish: the work done each second for the surfaces, which is projecting all 8,741
  points, encoding the SSE delta, and writing every changed value into the OPC UA address
  space.
- Fault Preview: a 60-minute preview of several faults, end to end as the API runs it.

The console's frame rate is measured in a browser; see docs/performance.md.
"""

import argparse
import asyncio
import json
import platform
import statistics
import sys
import time

from graphene_demo_twin.asset_model import load_asset_model
from graphene_demo_twin.event_log import golden_demo
from graphene_demo_twin.plant_design import load_plant_design
from graphene_demo_twin.sim import Simulation
from graphene_demo_twin.surfaces.api import frame_event
from graphene_demo_twin.surfaces.opcua import OpcUaSurface
from graphene_demo_twin.twin import Twin
from graphene_demo_twin.world import default_domains

START = 1_790_000_000
PREVIEWS = (
    ("CRAC/L1_CRAC3", "crac.fan_failure"),
    ("Cooling Towers Plant/R_P1_CT1", "tower.fan_failure"),
    ("Meter/SPPA Incomer 1", "utility.incomer_loss"),
    ("Chiller/R_C1", "chiller.trip"),
)


class Clock:
    def __init__(self) -> None:
        self.now = START + 0.25

    def __call__(self) -> float:
        return self.now


def stats(samples_s: list[float]) -> dict[str, float]:
    ms = sorted(1000.0 * s for s in samples_s)
    return {
        "mean_ms": statistics.fmean(ms),
        "p95_ms": ms[min(len(ms) - 1, int(0.95 * len(ms)))],
        "max_ms": ms[-1],
        "n": len(ms),
    }


def time_steps(sim: Simulation, n: int) -> list[float]:
    out = []
    for _ in range(n):
        t = time.perf_counter()
        sim.step()
        out.append(time.perf_counter() - t)
    return out


async def time_opcua(twin: Twin, clock: Clock, n: int) -> list[float]:
    surface = OpcUaSurface(twin, "127.0.0.1", 0)
    # Build the address space without listening: publish() is the per-second write.
    from asyncua import Server

    server = Server()
    await server.init()
    surface._server = server
    surface.namespace_index = await server.register_namespace("urn:bench")
    await surface._build()
    out = []
    for _ in range(n):
        clock.now += 1
        frame = twin.tick()
        t = time.perf_counter()
        await surface.publish(frame)
        out.append(time.perf_counter() - t)
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--quick", action="store_true", help="fewer samples")
    parser.add_argument("--json", action="store_true", help="print raw results as JSON")
    args = parser.parse_args()
    n = 60 if args.quick else 300

    t = time.perf_counter()
    asset_model = load_asset_model()
    design = load_plant_design(asset_model)
    load_s = time.perf_counter() - t

    results: dict[str, object] = {"load_s": load_s}
    sim = Simulation(design, default_domains(), 0, START)
    results["step_steady"] = stats(time_steps(sim, n))

    incident = Simulation(design, default_domains(), 0, START)
    for e in golden_demo().entries:
        incident.schedule(e.event(START))
    incident.run_until(START + 430)  # grid lost, one UPS on battery, tower fan failing
    results["step_incident"] = stats(time_steps(incident, n))

    clock = Clock()
    t = time.perf_counter()
    twin = Twin(asset_model, design, seed=0, clock=clock)
    results["twin_start_s"] = time.perf_counter() - t

    tick, sse = [], []
    for _ in range(n):
        clock.now += 1
        before = twin.frame
        t = time.perf_counter()
        frame = twin.tick()
        tick.append(time.perf_counter() - t)
        t = time.perf_counter()
        frame_event("delta", frame, before, twin.catalog)
        sse.append(time.perf_counter() - t)
    results["tick_step_project_publish"] = stats(tick)
    results["sse_delta_encode"] = stats(sse)
    results["opcua_write"] = stats(asyncio.run(time_opcua(twin, clock, max(10, n // 10))))

    previews = {}
    for target, fault in PREVIEWS[: 2 if args.quick else None]:
        t = time.perf_counter()
        preview = twin.preview_fault(target, fault, {}, 3600)
        previews[f"{fault} on {target}"] = {
            "seconds": time.perf_counter() - t,
            "affected": len(preview.affected),
            "diffs": len(preview.diffs),
        }
    results["preview_60min"] = previews

    if args.json:
        print(json.dumps(results, indent=2))
        return 0

    def row(name: str, s: dict[str, float], target: str) -> str:
        return (
            f"| {name} | {s['mean_ms']:.1f} | {s['p95_ms']:.1f} | {s['max_ms']:.1f} | "
            f"{s['n']} | {target} |"
        )

    print(f"Python {platform.python_version()} on {platform.machine()} ({platform.system()})")
    print(
        f"Asset Model + Plant Design load: {load_s:.2f} s; Twin start: "
        f"{results['twin_start_s']:.2f} s\n"
    )
    print("| Measure | mean ms | p95 ms | max ms | n | Target |")
    print("|---|---|---|---|---|---|")
    print(row("Live World step, steady", results["step_steady"], "≤ 1000 ms (1 s step)"))
    print(row("Live World step, Golden Demo incident", results["step_incident"], "≤ 1000 ms"))
    tick_row = results["tick_step_project_publish"]
    print(row("Twin tick: step + project 8,741 points + publish", tick_row, "≤ 1000 ms (1 Hz)"))
    print(row("SSE delta encode", results["sse_delta_encode"], "within the 1 Hz budget"))
    print(row("OPC UA write of one frame", results["opcua_write"], "within the 1 Hz budget"))
    print("\n| 60-minute Fault Preview | seconds | affected assets | point diffs | Target |")
    print("|---|---|---|---|---|")
    for name, p in previews.items():
        print(f"| {name} | {p['seconds']:.2f} | {p['affected']} | {p['diffs']} | < 3 s |")
    return 0


if __name__ == "__main__":
    sys.exit(main())
