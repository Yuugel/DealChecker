"""Five-second background scheduler; durable due times live in SQLite."""
import asyncio
import logging

log = logging.getLogger(__name__)


class Scheduler:
    def __init__(self, engine):
        self.engine = engine
        self._task = None
        self._stop = asyncio.Event()

    async def start(self):
        if self._task is None or self._task.done():
            self._stop.clear()
            self._task = asyncio.create_task(self._run())

    async def _run(self):
        while not self._stop.is_set():
            try:
                await self.engine.tick()
            except Exception:
                log.exception("scheduler tick failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=5)
            except asyncio.TimeoutError:
                pass

    async def stop(self):
        if self._task is not None:
            self._stop.set()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

