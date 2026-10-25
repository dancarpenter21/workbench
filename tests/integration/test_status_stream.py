import asyncio
import time

import pytest
from workbench_contracts import (
    ClawCurrentStatus,
    CommissionStatus,
    ManualStatus,
    PeripheralStatus,
    WorkbenchStatus,
)
from workbench_coordinator.status_stream import StatusPublisher


def snapshot(sequence):
    sample = ClawCurrentStatus(
        simulated=True,
        sample_sequence=sequence,
        age_seconds=0.01,
        bus_v=0,
        shunt_mv=0,
        reading_state="ok",
        power_state="off",
    )
    hat = PeripheralStatus(address=64, age_seconds=0.02)
    return WorkbenchStatus(
        mode="mock",
        operation=None,
        recovery_required=False,
        claw_current=sample,
        servo_hat=hat,
        manual=ManualStatus(simulated=True, claw_current=sample, servo_hat=hat),
        commissioning=CommissionStatus(claw_current=sample),
    )


async def test_viewers_share_refresh_and_slow_viewer_skips_old_snapshots():
    updates = asyncio.Queue()
    calls = 0

    async def refresh():
        nonlocal calls
        calls += 1
        return await updates.get()

    publisher = StatusPublisher(refresh, interval=0)
    async with publisher.subscribe():
        worker = publisher.task
        async with publisher.subscribe():
            assert publisher.task is worker
            assert publisher.viewers == 2
            await updates.put(snapshot(1))
            first, _ = await asyncio.wait_for(publisher.next(), 1)
            await updates.put(snapshot(2))
            second, _ = await asyncio.wait_for(publisher.next(first), 1)
            await updates.put(snapshot(3))
            third, _ = await asyncio.wait_for(publisher.next(second), 1)
            version, latest = await publisher.next(first)
            assert version == third
            assert latest.claw_current.sample_sequence == 3
            assert calls <= 4  # One acquisition loop, independent of viewer count.
        assert publisher.task is worker
    assert publisher.task is None
    assert publisher.viewers == 0


async def test_snapshot_age_includes_cache_time_once_and_never_mutates_source():
    async def refresh():
        raise AssertionError("No acquisition needed")

    publisher = StatusPublisher(refresh)
    publisher.status = original = snapshot(1)
    publisher.published_at = time.monotonic() - 2
    publisher.version = 1
    _, aged = await publisher.next()
    assert aged.claw_current.age_seconds == pytest.approx(2.01, abs=0.05)
    assert aged.manual.claw_current.age_seconds == aged.claw_current.age_seconds
    assert aged.commissioning.claw_current.age_seconds == aged.claw_current.age_seconds
    assert aged.servo_hat.age_seconds == pytest.approx(2.02, abs=0.05)
    assert original.claw_current.age_seconds == 0.01
    assert original.servo_hat.age_seconds == 0.02


async def test_new_subscription_waits_for_fresh_acquisition_after_idle():
    updates = asyncio.Queue()
    publisher = StatusPublisher(updates.get, interval=0)
    async with publisher.subscribe():
        await updates.put(snapshot(1))
        await asyncio.wait_for(publisher.next(), 1)
    async with publisher.subscribe():
        pending = asyncio.create_task(publisher.next())
        await asyncio.sleep(0)
        assert not pending.done()
        await updates.put(snapshot(2))
        _, received = await asyncio.wait_for(pending, 1)
        assert received.claw_current.sample_sequence == 2
