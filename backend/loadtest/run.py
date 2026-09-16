"""Load & concurrency numbers for the loop protocol (QRUIT_CLAUDE_CODE_LOOP.md
§4). Runs against the real local Postgres/Redis in full mock mode — no live
credentials needed (SKILL.md rule 5) — and prints throughput, p50/p95/p99
latency, and error rate per scenario. Not a pytest suite: these are numbers
to read and compare against an agreed target, not pass/fail assertions.

    python -m loadtest.run

Every scenario drives the shared ingestion/dedup/queue code paths through
the local_folder connector, which is representative of every other channel
(SKILL.md rule 4 — one shape for every source); provider-specific behaviour
(rate limits, auth errors) is already covered per-connector in tests/.
"""
from __future__ import annotations

import asyncio
import resource
import time
import uuid
from pathlib import Path

from app.bootstrap import build_container
from app.config import Settings
from app.models import InboundItem, Role
from app.worker import WorkerSettings
from arq.connections import RedisSettings
from arq.worker import Worker


def _settings() -> Settings:
    return Settings(
        qruit_data_dir=Path(f"/tmp/qruit-loadtest-{uuid.uuid4().hex}"),
        qruit_approval_secret="loadtest-secret-not-for-prod",
        arq_queue_name=f"loadtest:{uuid.uuid4().hex}",
    )


def report(name: str, latencies: list[float], elapsed: float, errors: int, rss_delta_mb: float) -> None:
    n = len(latencies)
    latencies = sorted(latencies)
    pct = lambda p: latencies[min(int(n * p), n - 1)] * 1000
    print(f"\n== {name} ==")
    print(f"  {n} ops in {elapsed:.2f}s -> {n / elapsed:.1f} ops/sec")
    print(f"  latency ms: p50={pct(0.50):.1f} p95={pct(0.95):.1f} p99={pct(0.99):.1f}")
    print(f"  errors: {errors}/{n}  rss growth: {rss_delta_mb:+.1f}MB")


async def run_with_concurrency(ops: list, concurrency: int) -> tuple[list[float], float, int]:
    """Runs `ops` (zero-arg async callables) with at most `concurrency` in
    flight at once. Returns (per-op latencies, wall-clock elapsed, errors)."""
    sem = asyncio.Semaphore(concurrency)
    latencies: list[float] = []
    errors = 0

    async def _run_one(op):
        nonlocal errors
        async with sem:
            start = time.monotonic()
            try:
                await op()
            except Exception:
                errors += 1
            latencies.append(time.monotonic() - start)

    start = time.monotonic()
    await asyncio.gather(*(_run_one(op) for op in ops))
    return latencies, time.monotonic() - start, errors


def _rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024


async def ingestion_throughput(n_cvs: int = 500, concurrency: int = 20) -> None:
    """§4.1: the doc's example peak — "500 CVs land in one hour" — ingested
    (fetch, normalize, classify, dedupe, store) at a much tighter concurrency
    than an hour would ever require, so the numbers reflect a worst case.
    Each CV sits in its own subfolder so `concurrency` concurrent
    ingest_for_role() calls (one per subfolder, as if N workers each pulled
    a different mailbox/folder at once) do genuinely distinct work instead
    of all racing to dedupe the same one file."""
    settings = _settings()
    container = await build_container(settings)
    role = Role(id=f"role-load-{uuid.uuid4().hex[:8]}")
    await container.storage.upsert_role(role)

    # dedup_index keys on (source, item_id) alone, with no TTL (CN-5) — so
    # the folder name must be unique per run, not just per file, or a
    # second run against the same persistent dev Postgres sees every item
    # as "already pulled" and ingests nothing.
    root = container.registry.get("local_folder").root
    for i in range(n_cvs):
        batch = root / f"{role.id}-batch_{i}"
        batch.mkdir()
        (batch / "cv.txt").write_text(f"Jane Doe {i}\njane{i}@example.com\nSkills: python, sql\n")

    rss_before = _rss_mb()
    latencies, elapsed, errors = await run_with_concurrency(
        [(lambda idx=i: container.ingestion.ingest_for_role(role, "local_folder", folder=f"{role.id}-batch_{idx}"))
         for i in range(n_cvs)],
        concurrency,
    )
    rss_after = _rss_mb()

    candidates = await container.storage.list_candidates(role.id)
    print(f"  distinct candidates ingested: {len(candidates)} (expected {n_cvs}, zero duplicates under concurrency)")
    report(f"ingestion_throughput(n={n_cvs}, concurrency={concurrency})", latencies, elapsed, errors, rss_after - rss_before)


async def queue_backlog_throughput(n_jobs: int = 500, worker_concurrency: int = 10) -> None:
    """§4.3: push far more jobs than workers can run at once and confirm the
    queue drains fully, reporting real drain throughput."""
    settings = _settings()
    container = await build_container(settings)
    role = Role(id=f"role-backlog-{uuid.uuid4().hex[:8]}")
    await container.storage.upsert_role(role)

    job_ids = [await container.jobs.enqueue("pull_for_role", role.id, "local_folder", "", "", 1) for _ in range(n_jobs)]

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
    start = time.monotonic()
    completed = await worker.run_check(max_burst_jobs=n_jobs * 2)
    elapsed = time.monotonic() - start
    await worker.close()

    statuses = [await container.jobs.status(job_id) for job_id in job_ids]
    failed = sum(1 for s in statuses if s["status"] != "complete")
    print(f"\n== queue_backlog_throughput(n={n_jobs}, worker_concurrency={worker_concurrency}) ==")
    print(f"  {completed} jobs drained in {elapsed:.2f}s -> {completed / elapsed:.1f} jobs/sec")
    print(f"  not-complete after drain: {failed}/{n_jobs}")


async def end_to_end_load(n_roles: int = 20, cvs_per_role: int = 10, concurrency: int = 5) -> None:
    """§4.4: the full chain — ingest (JD + CVs) -> classify -> dedupe ->
    screen -> shortlist checkpoint -> approve -> candidate_ack delivered —
    for `n_roles` roles at once, confirming checkpoints still fire correctly
    under concurrent load, not just for one role at a time."""
    settings = _settings()
    container = await build_container(settings)
    root = container.registry.get("local_folder").root

    async def _one_role(idx: int) -> None:
        role = Role(id=f"role-e2e-{idx}-{uuid.uuid4().hex[:6]}", client_contact=f"client{idx}@clientco.example")
        await container.storage.upsert_role(role)
        folder = root / role.id
        folder.mkdir()
        (folder / "role_jd.txt").write_text(
            "Job Description\nBackend Engineer\nSalary: $100k - $120k\n"
            "Location: Remote\nSeniority: mid-level\nRequirements: python\n"
        )
        for i in range(cvs_per_role):
            (folder / f"cv_{i}.txt").write_text(
                f"Curriculum Vitae\nProfessional Experience: {i} years.\n"
                f"Skills: python\ncandidate{i}@example.com\n"
            )

        await container.ingestion.ingest_for_role(role, "local_folder", folder=role.id, limit=cvs_per_role + 1)
        shortlisted = await container.screening.run(role)
        if not shortlisted:
            raise RuntimeError(f"{role.id}: screening produced no shortlist")

        proceed = InboundItem(
            source="gmail", item_id=f"{role.id}-proceed", name="reply",
            sender=role.client_contact, body_text="PROCEED",
        )
        outcome = await container.approval.process_inbound_item(proceed)
        if not outcome.applied:
            raise RuntimeError(f"{role.id}: PROCEED did not apply — {outcome.reason}")

    rss_before = _rss_mb()
    latencies, elapsed, errors = await run_with_concurrency(
        [(lambda idx=i: _one_role(idx)) for i in range(n_roles)], concurrency,
    )
    report(f"end_to_end_load(n_roles={n_roles}, cvs_per_role={cvs_per_role}, concurrency={concurrency})",
           latencies, elapsed, errors, _rss_mb() - rss_before)


async def soak(duration_seconds: int = 60, batch_size: int = 50) -> None:
    """§4.5: repeats the ingestion scenario back-to-back for `duration_seconds`
    against one long-lived container/connection pool, watching RSS for the
    slow climb a single short run wouldn't reveal. Default is a 60s smoke
    run; pass a longer duration (e.g. 3600+) for an actual soak."""
    settings = _settings()
    container = await build_container(settings)
    role = Role(id=f"role-soak-{uuid.uuid4().hex[:8]}")
    await container.storage.upsert_role(role)
    root = container.registry.get("local_folder").root

    rss_samples = [_rss_mb()]
    deadline = time.monotonic() + duration_seconds
    batches = 0
    while time.monotonic() < deadline:
        for i in range(batch_size):
            # dedup_index has no TTL (CN-5) — the role id makes each batch's
            # filenames unique across runs against the persistent dev Postgres.
            (root / f"{role.id}-cv_{batches}_{i}.txt").write_text(
                f"Jane Doe\njane{batches}_{i}@example.com\nSkills: python\n"
            )
        await container.ingestion.ingest_for_role(role, "local_folder", limit=batch_size)
        for f in root.glob(f"{role.id}-cv_*"):
            f.unlink()
        batches += 1
        rss_samples.append(_rss_mb())

    candidates = await container.storage.list_candidates(role.id)
    print(f"\n== soak(duration={duration_seconds}s) ==")
    print(f"  {batches} batches x {batch_size} CVs = {len(candidates)} candidates ingested")
    print(f"  rss: start={rss_samples[0]:.1f}MB end={rss_samples[-1]:.1f}MB "
          f"peak={max(rss_samples):.1f}MB (steady growth here = a leak to chase)")


async def main() -> None:
    await ingestion_throughput()
    await queue_backlog_throughput()
    await end_to_end_load()
    await soak()


if __name__ == "__main__":
    asyncio.run(main())
