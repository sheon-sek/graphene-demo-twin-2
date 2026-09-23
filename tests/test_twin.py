import asyncio
import threading

import pytest

from graphene_demo_twin.asset_model import SourceClass
from graphene_demo_twin.faults import FaultConflict, FaultError
from graphene_demo_twin.sim import Event, EventError
from graphene_demo_twin.twin import Frame, Twin
from graphene_demo_twin.world import SETTLING_S

START = 1_790_000_000
HOT_AISLE_DH03 = "Temperature and Humidity/Datahall 3/Sensor 17/Temp"
CRAC3 = "CRAC/L1_CRAC3"
CRAC1 = "CRAC/L1_CRAC1"
TRIP = {"fault": "crac.compressor_trip"}


class FakeClock:
    def __init__(self, now: float = START + 0.25) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def twin(asset_model, plant_design, clock) -> Twin:
    return Twin(asset_model, plant_design, seed=7, clock=clock)


def test_a_new_twin_publishes_its_initial_state(twin):
    frame = twin.frame
    assert frame.time == START == twin.live.time
    assert frame.seq == 0
    assert frame.projection.values.keys() == twin.asset_model.points.keys()


def test_tick_publishes_one_frame_per_live_world_step(twin, clock):
    assert twin.tick() is None  # the wall clock has not moved on

    clock.now += 1
    frame = twin.tick()
    assert frame is twin.frame
    assert (frame.seq, frame.time) == (1, START + 1)


def _published(twin: Twin, after: int = 0) -> list[Frame]:
    """Every frame published after `after`, in order, as a surface reading them would see."""

    async def read() -> list[Frame]:
        frames: list[Frame] = []
        seq = after
        while seq < twin.frame.seq:
            frames.append(frame := await twin.next_frame(seq))
            seq = frame.seq
        return frames

    return asyncio.run(read())


def test_a_stall_still_publishes_every_second_it_missed(twin, clock):
    clock.now += 5
    frame = twin.tick()
    assert (frame.seq, frame.time) == (5, START + 5)
    assert frame.projection.time == frame.state.time == twin.live.time

    frames = _published(twin)
    assert [f.time for f in frames] == [START + s for s in range(1, 6)]
    assert [f.seq for f in frames] == [1, 2, 3, 4, 5]
    assert all(f.projection.time == f.state.time == f.time for f in frames)
    temps = [f.projection.values[HOT_AISLE_DH03] for f in frames]
    assert len(set(temps)) > 1  # each frame is its own step, not the last one repeated


def test_a_reader_further_behind_than_the_backlog_gets_the_oldest_kept_frame(
    asset_model, plant_design, clock
):
    twin = Twin(asset_model, plant_design, seed=7, clock=clock, frame_backlog=3)
    clock.now += 5
    twin.tick()
    assert [f.time for f in _published(twin)] == [START + 3, START + 4, START + 5]


def test_frames_are_snapshots_the_live_world_cannot_change(twin, clock):
    frame = twin.frame
    before = dict(frame.state.assets["DH03"])
    clock.now += 60
    twin.tick()
    assert frame.state.assets["DH03"] == before
    assert twin.frame.state.assets["DH03"] != before


def test_a_submit_publishes_each_second_it_steps_then_the_new_event(twin, clock):
    clock.now += 3
    event = twin.submit("fault.inject", CRAC3, TRIP)  # catches up to START + 3
    assert event.at == START + 3

    frames = _published(twin)
    assert [f.time for f in frames] == [START + 1, START + 2, START + 3, START + 3]
    assert [f.events for f in frames] == [(), (), (), (event,)]
    assert frames[-1].event_count == 1
    # Logging an event changes no state, so its frame carries the same step.
    assert frames[-1].projection is frames[-2].projection
    assert twin.frame.events == twin.live.events
    assert twin.tick() is None


def test_fault_actions_publish_each_second_they_step_then_the_new_event(twin, clock):
    clock.now += 2
    injected = twin.inject_fault(CRAC3, "crac.compressor_trip", {})
    clock.now += 2
    cleared = twin.clear_fault(CRAC3, "crac.compressor_trip")

    frames = _published(twin)
    assert [f.time for f in frames] == [START + s for s in (1, 2, 2, 3, 4, 4)]
    assert [f.event_count for f in frames] == [0, 0, 1, 1, 1, 2]
    assert twin.frame.events == (injected, cleared) == twin.live.events


def test_creating_a_fork_publishes_each_second_it_steps(twin, clock):
    clock.now += 2
    session = twin.forks.create()
    assert session.fork.forked_at == START + 2
    assert [f.time for f in _published(twin)] == [START + 1, START + 2]


def test_a_fault_preview_publishes_each_second_it_steps(twin, clock):
    clock.now += 2
    twin.preview_fault(CRAC3, "crac.compressor_trip", {}, 60)
    assert [f.time for f in _published(twin)] == [START + 1, START + 2]


def test_reset_publishes_each_second_it_steps_before_the_new_epoch(twin, clock):
    clock.now += 2
    twin.reset()
    frames = _published(twin)
    assert [(f.epoch, f.time) for f in frames] == [(0, START + 1), (0, START + 2), (1, START + 2)]


def test_bad_operator_actions_are_rejected(twin):
    with pytest.raises(EventError):
        twin.submit("fault.inject", "CRAC/L1_CRAC9", TRIP)


def test_reset_publishes_a_fresh_world_in_a_new_epoch(asset_model, plant_design, twin, clock):
    twin.inject_fault(CRAC3, "crac.compressor_trip", {})
    twin.command(CRAC1, "mode", "hand")
    twin.command(CRAC1, "setpoint", 16.0)
    clock.now += 900
    last = twin.tick()
    assert twin.frame.projection.values[HOT_AISLE_DH03] > 27

    frame = twin.reset()
    assert frame is twin.frame
    assert frame.epoch == 1 and frame.seq == last.seq + 1
    assert frame.state.faults == {}
    assert twin.live.events == ()

    fresh = Twin(asset_model, plant_design, seed=7, clock=clock)
    assert frame.time == fresh.frame.time
    assert frame.projection.values == fresh.frame.projection.values
    assert twin.live.state_hash() == fresh.live.state_hash()
    for _ in range(120):
        clock.now += 1
        assert twin.tick().projection.values == fresh.tick().projection.values


def test_next_frame_wakes_waiters_on_publish_from_another_thread(twin, clock):
    async def exercise():
        waiter = asyncio.create_task(twin.next_frame(after=0))
        await asyncio.sleep(0.01)
        assert not waiter.done()

        def publish():
            clock.now += 1
            twin.tick()

        threading.Thread(target=publish).start()
        frame = await asyncio.wait_for(waiter, 2)
        assert frame.seq == 1
        # Already newer: returns at once.
        assert (await twin.next_frame(after=0)).seq == 1

    asyncio.run(exercise())


def test_close_releases_waiters(twin):
    async def exercise():
        waiter = asyncio.create_task(twin.next_frame(after=0))
        await asyncio.sleep(0.01)
        twin.close()
        assert await asyncio.wait_for(waiter, 2) is None
        assert await twin.next_frame(after=0) is None

    asyncio.run(exercise())


def test_run_publishes_on_each_wall_clock_second(twin, clock):
    async def exercise():
        stop = asyncio.Event()
        published = []

        async def fake_sleep(seconds: float) -> None:
            clock.now += seconds
            published.append(twin.frame.time)
            if len(published) == 4:
                stop.set()
            await asyncio.sleep(0)

        await twin.run(stop, sleep=fake_sleep)

    asyncio.run(exercise())
    assert twin.frame.time == START + 4
    assert twin.frame.seq == 4


def test_run_publishes_every_second_after_a_late_wakeup(twin, clock):
    async def exercise():
        stop = asyncio.Event()

        async def late_sleep(seconds: float) -> None:
            clock.now += seconds + 3  # the scheduler stalled for three more seconds
            stop.set()
            await asyncio.sleep(0)

        await twin.run(stop, sleep=late_sleep)

    asyncio.run(exercise())
    assert [f.time for f in _published(twin)] == [START + s for s in range(1, 5)]


def test_forks_run_ahead_without_touching_the_live_world(twin, clock):
    clock.now += 30
    twin.tick()
    live_hash = twin.live.state_hash()

    session = twin.forks.create()
    assert session.fork.forked_at == START + 30
    session.fork.schedule(Event(session.fork.time, "fault.inject", CRAC3, TRIP))
    session.fork.run_for(1800)
    preview = twin.projector.project(session.fork.state)

    assert preview.time == START + 30 + 1800
    assert preview.values[HOT_AISLE_DH03] > twin.frame.projection.values[HOT_AISLE_DH03] + 5
    assert twin.live.state_hash() == live_hash
    assert twin.live.events == ()
    assert twin.forks.get(session.id) is session
    twin.forks.delete(session.id)
    with pytest.raises(KeyError):
        twin.forks.get(session.id)


def test_the_oldest_fork_is_dropped_past_the_limit(asset_model, plant_design, clock):
    twin = Twin(asset_model, plant_design, seed=7, clock=clock, max_forks=2)
    a, b, c = (twin.forks.create() for _ in range(3))
    assert [s.id for s in twin.forks] == [b.id, c.id]
    assert len({a.id, b.id, c.id}) == 3


def test_reset_discards_forks(twin):
    twin.forks.create()
    twin.reset()
    assert list(twin.forks) == []


# ---- Faults and Operator Commands


def test_faults_are_injected_on_exactly_the_chosen_asset(twin, clock):
    event = twin.inject_fault(CRAC3, "crac.fan_failure", {"severity": 0.5, "ramp_min": 2})
    assert (event.kind, event.target) == ("fault.inject", CRAC3)
    assert event.params == {
        "fault": "crac.fan_failure",
        "severity": 0.5,
        "ramp_min": 2,
        "auto_clear_min": None,
    }
    clock.now += 60
    faults = twin.tick().state.faults
    assert list(faults) == [f"crac.fan_failure@{CRAC3}"]
    assert faults[f"crac.fan_failure@{CRAC3}"]["target"] == CRAC3


def test_fault_actions_are_validated_before_they_reach_the_event_log(twin):
    with pytest.raises(FaultError, match="applies to CRAC"):
        twin.inject_fault("Temperature and Humidity/Datahall 3/Sensor 17", "crac.fan_failure", {})
    with pytest.raises(FaultError, match="severity"):
        twin.inject_fault(CRAC3, "crac.fan_failure", {"severity": 3})
    with pytest.raises(FaultConflict, match="not active"):
        twin.clear_fault(CRAC3, "crac.fan_failure")
    twin.inject_fault(CRAC3, "crac.fan_failure", {})
    with pytest.raises(FaultConflict, match="already active"):  # logged, not yet stepped
        twin.inject_fault(CRAC3, "crac.fan_failure", {})
    twin.clear_fault(CRAC3, "crac.fan_failure")  # clearing a fault logged to start is fine
    with pytest.raises(FaultConflict, match="not active"):
        twin.clear_fault(CRAC3, "crac.fan_failure")
    assert [e.kind for e in twin.live.events] == ["fault.inject", "fault.clear"]


def test_operator_commands_are_logged_and_validated(twin, clock):
    event = twin.command(CRAC3, "mode", "hand")
    assert (event.kind, event.target, dict(event.params)) == (
        "command",
        CRAC3,
        {"command": "mode", "value": "hand"},
    )
    with pytest.raises(EventError, match="setpoint"):
        twin.command(CRAC3, "setpoint", 99)
    with pytest.raises(EventError, match="no Operator Commands"):
        twin.command("Temperature and Humidity/Datahall 3/Sensor 17", "run", False)
    assert [c.name for c in twin.commands["CRAC"]] == ["mode", "run", "setpoint"]
    clock.now += 1
    assert twin.tick().state.assets[CRAC3]["mode"] == "hand"


@pytest.mark.parametrize("minutes", [15, 30, 60])
def test_a_preview_predicts_what_the_live_world_then_shows(twin, clock, minutes):
    """Same seed, same Event Log, same duration: the preview is what the Live World shows."""
    twin.inject_fault(CRAC1, "crac.fan_failure", {"severity": 0.6, "auto_clear_min": 10})
    clock.now += 120
    twin.tick()

    twin.command(CRAC3, "setpoint", 16.0)  # logged, takes effect on the next step
    params = {"severity": 0.9, "ramp_min": 5}
    preview = twin.preview_fault(CRAC3, "crac.compressor_trip", params, minutes * 60)
    assert preview.diffs and preview.alarms
    twin.inject_fault(CRAC3, "crac.compressor_trip", params)
    clock.now += minutes * 60
    frame = twin.tick()

    assert frame.time == preview.end
    live = frame.projection
    for d in preview.diffs:
        assert live.values[d.path] == d.predicted, d.path
        assert live.quality(d.path) is d.predicted_quality, d.path
    for a in preview.alarms:
        assert live.values[a.path] == a.predicted, a.path


def test_clearing_all_faults_returns_the_live_world_to_the_base_world(
    asset_model, plant_design, twin, clock
):
    base = Twin(asset_model, plant_design, seed=7, clock=clock)
    twin.inject_fault(CRAC3, "crac.compressor_trip", {})
    twin.inject_fault(CRAC1, "crac.setpoint_drift", {"severity": 0.5})
    twin.inject_fault("Temperature and Humidity/Datahall 3/Sensor 17", "th.stuck", {})
    clock.now += 1200
    for fault in list(twin.tick().state.faults.values()):
        twin.clear_fault(fault["target"], fault["fault"])
    clock.now += SETTLING_S
    faulted, clean = twin.tick(), base.tick()
    assert faulted.state.faults == {}
    for path, value in clean.projection.values.items():
        point = asset_model.point(path)
        if point.source_class is SourceClass.ENERGY_INTEGRAL or path.endswith(
            ("(Daily)", "(Monthly)", "(Annually)")
        ):
            continue  # history remembers what the faults cost
        if isinstance(value, float):
            assert faulted.projection.values[path] == pytest.approx(value, rel=5e-3, abs=0.02)
        else:
            assert faulted.projection.values[path] == value, path
    assert faulted.projection.degraded == clean.projection.degraded
