from __future__ import annotations

import os
import uuid

# pytest-postgresql discovers initdb/pg_ctl/psql via PATH; this repo's Postgres
# lives under /usr/lib/postgresql/14/bin, which isn't on PATH by default.
os.environ["PATH"] = "/usr/lib/postgresql/14/bin:" + os.environ.get("PATH", "")

import pytest
from arq.connections import RedisSettings
from arq.worker import Worker
from fastapi.testclient import TestClient
from pytest_postgresql import factories

from app.bootstrap import build_container, get_container
from app.config import Settings
from app.main import app
from app.worker import WorkerSettings

postgresql_proc = factories.postgresql_proc(port=None)
postgresql = factories.postgresql("postgresql_proc")


@pytest.fixture
def settings(tmp_path, postgresql):
    info = postgresql.info
    database_url = f"postgresql+psycopg://{info.user}@{info.host}:{info.port}/{info.dbname}"
    return Settings(
        qruit_data_dir=tmp_path,
        qruit_approval_secret="test-secret-not-for-prod",
        qruit_email_mock=True,
        qruit_mock_api=True,
        qruit_interview_mock=True,
        whatsapp_mock=True,
        database_url=database_url,
        # Redis itself isn't test-isolated like the ephemeral Postgres above
        # (it's the real local instance) — a unique queue name per test keeps
        # jobs from one test invisible to the next.
        arq_queue_name=f"test:{uuid.uuid4().hex}",
    )


@pytest.fixture
async def container(settings):
    return await build_container(settings)


@pytest.fixture
def client(container):
    app.dependency_overrides[get_container] = lambda: container
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def run_worker_burst(settings):
    """Drains whatever's currently queued on this test's queue by running a
    real arq Worker in burst mode (stops once the queue is empty) — proves
    jobs actually execute, not just that enqueue() returns a job id."""

    async def _run(max_burst_jobs: int = 1000, worker_concurrency: int = 10) -> int:
        worker = Worker(
            functions=WorkerSettings.functions,
            redis_settings=RedisSettings.from_dsn(settings.redis_url),
            queue_name=settings.arq_queue_name,
            on_startup=WorkerSettings.on_startup,
            on_shutdown=WorkerSettings.on_shutdown,
            ctx={"settings": settings},
            burst=True,
            max_jobs=worker_concurrency,
        )
        try:
            return await worker.run_check(max_burst_jobs=max_burst_jobs)
        finally:
            await worker.close()

    return _run
