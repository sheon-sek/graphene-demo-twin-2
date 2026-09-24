import asyncio
import pickle
import time

import pytest

from graphene_demo_twin.sim import (
    Event,
    EventError,
    LiveWorld,
    Noise,
    Simulation,
    WhatIfFork,
    WorldState,
    state_hash,
)
from graphene_demo_twin.sim.it_load import ITLoadDomain
from graphene_demo_twin.sim.site import SITE
from graphene_demo_twin.sim.thermal import PLANT
from graphene_demo_twin.sim.water import WATER
from graphene_demo_twin.world import default_domains

START = 1_790_000_000
"""An arbitrary whole-second sim start time (2026-09-21)."""


class FakeClock:
    def __init__(self, now: float = START + 0.25) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class CountingDomain(ITLoadDomain):
    """The IT Load domain, counting how often the engine asks it to step."""

    def __init__(self) -> None:
        self.steps = 0

    def step(self, state, ctx) -> None:
        self.steps += 1
        super().step(state, ctx)


CRAC = {  # the CRAC serving each Data Hall
    "DH01": "CRAC/L1_CRAC1",
    "DH02": "CRAC/L1_CRAC2",
    "DH03": "CRAC/L1_CRAC3",
    "DH04": "CRAC/L1_CRAC4",
    "DH05": "CRAC/R_CRAC1",
    "DH06": "CRAC/R_CRAC2",
    "DH07": "CRAC/R_CRAC3",
    "DH08": "CRAC/R_CRAC4",
}
FAN_FAILURE = {"fault": "crac.fan_failure"}


def _sim(plant_design, seed: int = 7, start: int = START, domains=None) -> Simulation:
    return Simulation(plant_design, domains or default_domains(), seed, start)


def _live(plant_design, seed: int, clock) -> LiveWorld:
    return LiveWorld(plant_design, default_domains(), seed=seed, clock=clock)


def _inject(at: int, hall: str = "DH03", severity: float = 1.0) -> Event:
    return Event(at, "fault.inject", CRAC[hall], {**FAN_FAILURE, "severity": severity})


def _clear(at: int, hall: str = "DH03") -> Event:
    return Event(at, "fault.clear", CRAC[hall], FAN_FAILURE)


def _key(hall: str) -> str:
    return f"crac.fan_failure@{CRAC[hall]}"


def _trajectory(sim: Simulation, steps: int, every: int = 1) -> list[str]:
    hashes = []
    for i in range(steps):
        sim.step()
        if i % every == 0:
            hashes.append(sim.state_hash())
    return hashes


# ---- Noise


def test_noise_is_keyed_by_seed_key_and_time_not_by_call_order():
    a, b = Noise(1), Noise(1)
    first = [a.uniform("DH01/load", t) for t in range(5)]
    a.uniform("something else", 99)
    assert [b.uniform("DH01/load", t) for t in reversed(range(5))] == first[::-1]
    assert Noise(2).uniform("DH01/load", 0) != first[0]
    assert a.uniform("DH02/load", 0) != first[0]
    assert all(0.0 <= u < 1.0 for u in first)


def test_noise_gauss_is_roughly_standard_normal():
    noise = Noise(3)
    xs = [noise.gauss("k", t) for t in range(20_000)]
    mean = sum(xs) / len(xs)
    var = sum((x - mean) ** 2 for x in xs) / len(xs)
    assert abs(mean) < 0.03
    assert abs(var - 1.0) < 0.05


# ---- State and hashing


def test_state_hash_covers_time_values_and_types():
    s = WorldState(time=10, assets={"DH01": {"t": 1.0, "on": True}})
    same = WorldState(time=10, assets={"DH01": {"on": True, "t": 1.0}})
    assert state_hash(s) == state_hash(same)
    assert state_hash(s) != state_hash(WorldState(11, {"DH01": {"t": 1.0, "on": True}}))
    assert state_hash(s) != state_hash(WorldState(10, {"DH01": {"t": 1.0 + 1e-15, "on": True}}))
    assert state_hash(s) != state_hash(WorldState(10, {"DH01": {"t": 1, "on": True}}))
    assert state_hash(s) != state_hash(WorldState(10, {"DH01": {"t": 1.0, "on": 1}}))
    faulted = WorldState(10, {"DH01": {"t": 1.0, "on": True}}, {"f@a": {"level": 0.5}})
    assert state_hash(faulted) != state_hash(s)
    assert state_hash(faulted) != state_hash(
        WorldState(10, {"DH01": {"t": 1.0, "on": True}}, {"f@a": {"level": 0.25}})
    )
    copy = faulted.copy()
    copy.faults["f@a"]["level"] = 1.0
    assert faulted.faults["f@a"]["level"] == 0.5


# ---- Stepping


def test_initial_state_is_steady_and_starts_at_the_start_time(plant_design):
    sim = _sim(plant_design)
    assert sim.time == START
    halls = [r.id for r in plant_design.rooms.values() if r.kind == "hall"]
    nodes = {*plant_design.assets, *plant_design.rooms, SITE, PLANT, WATER}
    assert set(halls) <= sim.state.assets.keys() <= nodes
    assert sim.state.faults == {}
    before = {h: sim.state.assets[h]["temp_c"] for h in halls}
    sim.advance(600)
    assert sim.time == START + 600
    for h in halls:  # only noise moves a steady state
        assert sim.state.assets[h]["temp_c"] == pytest.approx(before[h], abs=0.5)


@pytest.mark.slow
def test_same_seed_and_event_log_give_bit_identical_trajectories(plant_design):
    runs = []
    for _ in range(2):
        sim = _sim(plant_design, seed=42)
        for e in (_inject(START + 100), _clear(START + 900), _inject(START + 1500, "DH08", 1.0)):
            sim.schedule(e)
        runs.append(_trajectory(sim, 3000, every=10))
    assert runs[0] == runs[1]
    assert len(set(runs[0])) == len(runs[0])


def test_a_different_seed_or_event_log_changes_the_trajectory(plant_design):
    base = _trajectory(_sim(plant_design, seed=42), 100)
    assert _trajectory(_sim(plant_design, seed=43), 100) != base
    faulted = _sim(plant_design, seed=42)
    faulted.schedule(_inject(START + 50))
    other = _trajectory(faulted, 100)
    assert other[:50] == base[:50]
    assert other[50:] != base[50:]


def test_events_apply_at_their_sim_time_in_log_order(plant_design):
    sim = _sim(plant_design)
    sim.schedule(_inject(START + 5, severity=0.2))
    sim.schedule(_inject(START + 5, severity=0.9))
    sim.advance(5)
    assert sim.state.faults == {}
    sim.step()
    assert sim.state.faults[_key("DH03")]["level"] == 0.9
    assert [e.params["severity"] for e in sim.events] == [0.2, 0.9]


def test_events_cannot_be_scheduled_in_the_past_or_for_unknown_mechanisms(plant_design):
    sim = _sim(plant_design)
    sim.advance(10)
    with pytest.raises(EventError, match="past"):
        sim.schedule(_inject(START + 9))
    with pytest.raises(EventError, match="no domain"):
        sim.schedule(Event(START + 10, "fault.inject", "DH03", {"fault": "nope"}))
    with pytest.raises(EventError, match="no domain"):
        sim.schedule(Event(START + 10, "fault.inject", "CRAC/L1_CRAC9", FAN_FAILURE))
    sim.schedule(_inject(START + 10))  # the current instant is still open


def test_event_params_are_frozen_scalars_so_the_log_cannot_change_after_scheduling(plant_design):
    params = {"fault": "placeholder.cooling_loss", "severity": 0.5}
    event = Event(START, "fault.inject", "DH03", params)
    params["severity"] = 1.0
    assert event.params["severity"] == 0.5
    with pytest.raises(TypeError):
        event.params["severity"] = 1.0  # type: ignore[index]
    for nested in ([], {}, ("a",), {1, 2}, object()):
        with pytest.raises(EventError, match="scalar"):
            Event(START, "fault.inject", "DH03", {"fault": "x", "meta": nested})
    with pytest.raises(EventError, match="names"):
        Event(START, "fault.inject", "DH03", {1: "x"})


def test_clear_recovers_through_dynamics_rather_than_snapping_back(plant_design):
    sim = _sim(plant_design)
    steady = sim.state.assets["DH03"]["temp_c"]
    sim.schedule(_inject(START))
    sim.advance(1800)
    hot = sim.state.assets["DH03"]["temp_c"]
    assert hot > steady + 3
    sim.schedule(_clear(sim.time))
    sim.step()
    assert sim.state.assets["DH03"]["temp_c"] > hot - 1
    sim.advance(7200)
    assert sim.state.assets["DH03"]["temp_c"] == pytest.approx(steady, abs=0.5)


# ---- Energy integrals (v1 #8)


def test_energy_integrals_accumulate_step_by_step(plant_design):
    sim = _sim(plant_design)
    for _ in range(100):
        before = sim.state.assets["~IT-DH01"]["energy_kwh"]
        sim.step()
        it = sim.state.assets["~IT-DH01"]
        assert it["energy_kwh"] == before + it["power_kw"] / 3600.0


def test_reaching_any_instant_never_reintegrates_history(plant_design):
    domain = CountingDomain()
    sim = _sim(plant_design, domains=[domain])
    sim.advance(10)
    size_early = len(pickle.dumps(sim.state))
    sim.advance(5000)
    assert domain.steps == 5010
    sim.step()
    assert domain.steps == 5011  # one step costs one step, however long the history
    sim.state_hash()
    sim.fork()
    assert domain.steps == 5011  # reading and forking never step
    assert len(pickle.dumps(sim.state)) == size_early  # no timeline accumulates in state


# ---- Live World


def test_live_world_is_locked_to_wall_clock_at_1x(plant_design):
    clock = FakeClock(START + 0.9)
    live = _live(plant_design, 1, clock)
    assert live.time == START
    clock.now = START + 1.0
    assert live.catch_up() == 1
    assert live.time == START + 1
    clock.now = START + 61.5
    assert live.catch_up() == 60
    assert live.catch_up() == 0
    assert live.time == START + 61
    assert not hasattr(live, "pause")
    assert not hasattr(live, "advance")


def test_live_world_submit_stamps_operator_actions_with_the_current_sim_time(plant_design):
    clock = FakeClock()
    live = _live(plant_design, 1, clock)
    clock.now += 30
    event = live.submit("fault.inject", CRAC["DH02"], FAN_FAILURE)
    assert event.at == START + 30
    assert live.events == (event,)
    clock.now += 1
    live.catch_up()
    assert live.state.faults[_key("DH02")]["level"] == 1.0


def test_live_world_matches_an_offline_replay_of_its_event_log(plant_design):
    clock = FakeClock()
    live = _live(plant_design, 5, clock)
    for dt in (13, 400, 17, 900):
        clock.now += dt
        live.submit("fault.inject", CRAC["DH05"], FAN_FAILURE)
        clock.now += dt
        live.submit("fault.clear", CRAC["DH05"], FAN_FAILURE)
    clock.now += 100
    live.catch_up()

    replay = _sim(plant_design, seed=5)
    for e in live.events:
        replay.schedule(e)
    replay.run_until(live.time)
    assert replay.state_hash() == live.state_hash()


@pytest.mark.slow
def test_reset_is_indistinguishable_from_a_fresh_process(plant_design):
    clock = FakeClock()
    live = _live(plant_design, 9, clock)
    halls = ["DH01", "DH04", "DH06", "DH08"]
    for i in range(36):  # six hours of inject/clear cycles
        clock.now += 300
        live.submit("fault.inject", CRAC[halls[i % 4]], FAN_FAILURE)
        live.submit("command", CRAC[halls[i % 4]], {"command": "setpoint", "value": 17.0 + i % 3})
        clock.now += 300
        if i % 3:
            live.submit("fault.clear", CRAC[halls[i % 4]], FAN_FAILURE)
    clock.now += 0.5
    live.catch_up()
    assert live.events

    live.reset()
    fresh = _live(plant_design, 9, clock)
    assert live.events == fresh.events == ()
    assert live.time == fresh.time
    assert live.state == fresh.state
    assert live.state_hash() == fresh.state_hash()
    for _ in range(600):
        clock.now += 1
        live.catch_up()
        fresh.catch_up()
        assert live.state_hash() == fresh.state_hash()


def test_live_world_state_is_a_snapshot_that_cannot_bypass_the_event_log(plant_design):
    clock = FakeClock()
    live = _live(plant_design, 9, clock)
    before = live.state_hash()
    snapshot = live.state
    temp = snapshot.assets["DH01"]["temp_c"]
    snapshot.assets["DH01"]["temp_c"] = temp + 10.0
    snapshot.faults["crac.fan_failure@" + CRAC["DH01"]] = {"level": 1.0}
    snapshot.time += 100
    assert live.state.assets["DH01"]["temp_c"] == temp
    assert live.state.faults == {}
    assert live.time == START
    assert live.state_hash() == before
    assert live.events == ()


def test_reset_never_rewinds_sim_time_when_the_wall_clock_moved_back(plant_design):
    clock = FakeClock(START + 110.5)
    live = _live(plant_design, 9, clock)
    inject = _inject(START + 110, "DH02")
    live.submit(inject.kind, inject.target, inject.params)
    clock.now = START + 90.5  # e.g. an NTP correction
    live.reset()
    assert live.events == ()
    assert live.time == START + 110  # SourceTimestamps stay monotonic
    assert live.state_hash() == _sim(plant_design, seed=9, start=START + 110).state_hash()
    clock.now = START + 111.0
    assert live.catch_up() == 1


# ---- What-if Forks


def test_a_fork_without_hypothetical_events_tracks_the_live_world_exactly(plant_design):
    clock = FakeClock()
    live = _live(plant_design, 2, clock)
    clock.now += 120
    live.submit("fault.inject", CRAC["DH01"], FAN_FAILURE)
    fork = live.fork()
    assert isinstance(fork, WhatIfFork)
    assert fork.forked_at == live.time
    fork.advance(300)
    clock.now += 300
    live.catch_up()
    assert fork.state_hash() == live.state_hash()


def test_a_fork_is_paused_between_calls_and_isolated_from_the_live_world(plant_design):
    clock = FakeClock()
    live = _live(plant_design, 2, clock)
    clock.now += 60
    live.catch_up()
    fork = live.fork()
    t = fork.time
    clock.now += 60
    live.catch_up()
    assert fork.time == t  # it moves only when asked

    fork.schedule(_inject(fork.time, "DH07"))
    fork.advance(600)
    assert fork.state.faults[_key("DH07")]["level"] > 0
    assert live.events == ()
    assert live.state.faults == {}

    live.submit("fault.inject", CRAC["DH02"], FAN_FAILURE)
    assert len(fork.events) == 1


def test_a_60_minute_fork_runs_in_under_3_s_and_leaves_the_live_world_untouched(plant_design):
    clock = FakeClock()
    live = _live(plant_design, 11, clock)
    clock.now += 3600
    live.submit("fault.inject", CRAC["DH04"], FAN_FAILURE)
    before = live.state_hash()

    started = time.perf_counter()
    fork = live.fork()
    fork.schedule(_inject(fork.time, "DH08", 0.8))
    fork.run_for(3600)
    elapsed = time.perf_counter() - started

    assert fork.time == live.time + 3600
    assert elapsed < 3.0
    assert live.state_hash() == before
    assert len(live.events) == 1


def test_live_world_run_steps_once_per_wall_clock_second(plant_design):
    clock = FakeClock(START + 0.5)
    live = _live(plant_design, 1, clock)
    ticks: list[int] = []
    stop = asyncio.Event()
    delays: list[float] = []

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)
        clock.now += delay

    def on_tick(world: LiveWorld) -> None:
        ticks.append(world.time)
        if len(ticks) == 5:
            stop.set()

    asyncio.run(live.run(stop, on_tick=on_tick, sleep=fake_sleep))
    assert ticks == [START + 1 + i for i in range(5)]
    assert delays[0] == pytest.approx(0.5)


def test_live_world_run_publishes_every_second_after_a_delayed_wakeup(plant_design):
    clock = FakeClock(START + 0.5)
    live = _live(plant_design, 1, clock)
    ticks: list[tuple[int, str]] = []
    stop = asyncio.Event()

    async def stalled_sleep(delay: float) -> None:
        clock.now += delay + 4  # the event loop was blocked for four extra seconds

    def on_tick(world: LiveWorld) -> None:
        ticks.append((world.time, world.state_hash()))
        stop.set()

    asyncio.run(live.run(stop, on_tick=on_tick, sleep=stalled_sleep))
    assert [t for t, _ in ticks] == [START + 1 + i for i in range(5)]
    replay = _sim(plant_design, seed=1)
    assert [h for _, h in ticks] == _trajectory(replay, 5)
