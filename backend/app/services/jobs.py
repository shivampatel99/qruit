from __future__ import annotations

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings
from arq.jobs import Job, JobStatus

from app.config import Settings


class JobQueue:
    """Thin wrapper around arq's Redis pool: enqueues a named job (defined in
    app/worker.py, picked up by a running `arq app.worker.WorkerSettings`
    process) and reads back its status/result by job id. One pool per
    process, lazily connected on first use."""

    def __init__(self, settings: Settings, queue_name: str | None = None):
        self.settings = settings
        self.queue_name = queue_name or settings.arq_queue_name
        self._pool: ArqRedis | None = None

    async def _get_pool(self) -> ArqRedis:
        if self._pool is None:
            self._pool = await create_pool(
                RedisSettings.from_dsn(self.settings.redis_url), default_queue_name=self.queue_name,
            )
        return self._pool

    async def enqueue(self, job_name: str, *args) -> str:
        pool = await self._get_pool()
        job = await pool.enqueue_job(job_name, *args, _queue_name=self.queue_name)
        assert job is not None  # only None on a reused _job_id, which we never set
        return job.job_id

    async def status(self, job_id: str) -> dict:
        pool = await self._get_pool()
        job = Job(job_id, pool, _queue_name=self.queue_name)
        current = await job.status()
        if current == JobStatus.not_found:
            return {"status": "not_found"}
        if current in (JobStatus.queued, JobStatus.deferred, JobStatus.in_progress):
            return {"status": current.value}
        info = await job.result_info()
        if info is None:
            return {"status": current.value}
        if info.success:
            return {"status": "complete", "result": info.result}
        return {"status": "failed", "error": str(info.result)}
