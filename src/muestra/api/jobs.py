"""Bounded serial worker. Running native audio calls finish safely before shutdown."""

from __future__ import annotations

import asyncio
from uuid import uuid4

from ..schemas.api import Job
from .core import Runtime, ToolSpec, failure, normalized_progress
from .events import EventBus


class JobQueueFull(Exception):
    pass


class Jobs:
    def __init__(self, runtime: Runtime, bus: EventBus, *, capacity=32, history=256):
        self.runtime, self.bus = runtime, bus
        self.pending = asyncio.Queue(maxsize=capacity)
        self.items: dict[str, Job] = {}
        self.history = history
        self.accepting = True

    def submit(self, spec: ToolSpec, params: dict) -> Job:
        if not self.accepting or self.pending.full():
            raise JobQueueFull
        while len(self.items) >= self.history:
            oldest = next(
                (key for key, job in self.items.items() if job.status in ("done", "failed")), None
            )
            if oldest is None:
                raise JobQueueFull
            del self.items[oldest]
        job = Job(id=uuid4().hex, tool=spec.name, status="queued")
        self.items[job.id] = job
        self.pending.put_nowait((job, spec, params))
        self.updated(job)
        return job

    def updated(self, job: Job):
        self.bus.publish("job.updated", job.model_dump(mode="json"))

    def cancel(self, job: Job) -> bool:
        if job.status != "queued":
            return False
        job.status = "failed"
        job.message = "Cancelled before starting."
        job.result = failure(
            job.tool, "cancelled", job.message, "Submit the tool again to retry."
        ).model_dump(mode="json")
        self.updated(job)
        return True

    async def worker(self):
        loop = asyncio.get_running_loop()
        while True:
            item = await self.pending.get()
            if item is None:
                self.pending.task_done()
                return
            job, spec, params = item
            if job.status == "failed":
                self.pending.task_done()
                continue
            job.status, job.progress = "running", 0.0
            self.updated(job)

            def update(value, message, job=job):
                if job.status == "running":
                    job.progress = normalized_progress(value)
                    job.message = message
                    self.updated(job)

            def progress(value, message=None):
                loop.call_soon_threadsafe(update, value, message)

            result = await asyncio.to_thread(self.runtime.call, spec, params, progress=progress)
            job.result = result.model_dump(mode="json")
            job.status = "done" if result.ok else "failed"
            job.progress = 1.0 if result.ok else job.progress
            self.updated(job)
            self.pending.task_done()

    async def close(self):
        self.accepting = False
        for job in self.items.values():
            self.cancel(job)
        await self.pending.put(None)
