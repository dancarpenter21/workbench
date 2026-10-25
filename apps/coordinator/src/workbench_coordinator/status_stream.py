"""Share status acquisition across viewers and retain only the newest snapshot."""

import asyncio
import logging
import time
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)


class StatusPublisher:
    def __init__(self, refresh, interval=0.1):
        self.refresh = refresh
        self.interval = interval
        self.condition = asyncio.Condition()
        self.version = 0
        self.status = None
        self.published_at = None
        self.viewers = 0
        self.task = None

    @asynccontextmanager
    async def subscribe(self):
        self.viewers += 1
        if self.task is None:
            self.status = None
            self.published_at = None
            self.task = asyncio.create_task(self.run())
        try:
            yield self
        finally:
            self.viewers -= 1
            if self.viewers == 0:
                await self.close()

    async def close(self):
        task, self.task = self.task, None
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def run(self):
        while True:
            started = time.monotonic()
            try:
                status = await self.refresh()
                async with self.condition:
                    self.status = status
                    self.published_at = time.monotonic()
                    self.version += 1
                    self.condition.notify_all()
            except Exception:
                logger.exception("Status refresh failed")
            await asyncio.sleep(max(0, self.interval - (time.monotonic() - started)))

    async def next(self, after=0):
        async with self.condition:
            await self.condition.wait_for(lambda: self.status is not None and self.version > after)
            version = self.version
            status = self.status.model_copy(deep=True)
            elapsed = time.monotonic() - self.published_at
        # A cached snapshot must age while a slow viewer is acknowledging its predecessor.
        samples = [status.claw_current, status.servo_hat]
        if status.hardware:
            samples.extend([status.hardware.claw_current, status.hardware.servo_hat])
        if status.manual:
            samples.extend([status.manual.claw_current, status.manual.servo_hat])
        if status.commissioning:
            samples.append(status.commissioning.claw_current)
        seen = set()
        for sample in samples:
            if sample is not None and id(sample) not in seen:
                seen.add(id(sample))
                if sample.age_seconds is not None:
                    sample.age_seconds += elapsed
        return version, status
