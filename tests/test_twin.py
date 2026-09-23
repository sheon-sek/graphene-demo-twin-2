import asyncio
import threading

import pytest

from graphene_demo_twin.sim import Event, EventError
from graphene_demo_twin.twin import Twin

START = 1_790_000_000
HOT_AISLE_DH03 = "Temperature and Humidity/Datahall 3/Sensor 17/Temp"
COOLING_LOSS = {"fault": "placeholder.cooling_loss", "severity": 0.8}


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

    clock.now += 5  # catch-up after a stall publishes once, at the latest second
    frame = twin.tick()
    assert (frame.seq, frame.time) == (2, START + 6)
    assert frame.projection.time == frame.state.time == twin.live.time


def test_frames_are_snapshots_the_live_world_cannot_change(twin, clock):
    frame = twin.frame
    before = dict(frame.state.assets["DH03"])
    clock.now += 60
    twin.tick()
    assert frame.state.assets["DH03"] == before
    assert twin.frame.state.assets["DH03"] != before


def test_a_submit_that_steps_the_world_is_still_published(twin, clock):
    clock.now += 1
    twin.submit("fault.inject", "DH03", COOLING_LOSS)  # catches up to START + 1 itself
    assert twin.live.time == START + 1
    assert twin.frame.time == START
    assert twin.tick().time == START + 1
    assert [e.kind for e in twin.live.events] == ["fault.inject"]


def test_bad_operator_actions_are_rejected(twin):
    with pytest.raises(EventError):
        twin.submit("fault.inject", "DH99", COOLING_LOSS)


def test_reset_publishes_a_fresh_world_in_a_new_epoch(asset_model, plant_design, twin, clock):
    twin.submit("fault.inject", "DH03", COOLING_LOSS)
    clock.now += 900
    twin.tick()
    assert twin.frame.projection.values[HOT_AISLE_DH03] > 30

    frame = twin.reset()
    assert frame is twin.frame
    assert frame.epoch == 1 and frame.seq == 2
    assert twin.live.events == ()

    fresh = Twin(asset_model, plant_design, seed=7, clock=clock)
    assert frame.time == fresh.frame.time
    assert frame.projection.values == fresh.frame.projection.values


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


def test_forks_run_ahead_without_touching_the_live_world(twin, clock):
    clock.now += 30
    twin.tick()
    live_hash = twin.live.state_hash()

    session = twin.forks.create()
    assert session.fork.forked_at == START + 30
    session.fork.schedule(Event(session.fork.time, "fault.inject", "DH03", COOLING_LOSS))
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
