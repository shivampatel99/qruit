from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor

from app.models import InboundItem, Role, RoleStatus
from tests.test_pipeline import CV_TEXT, _shortlisted_role_with_candidates


def _write_local(container, filename: str, text: str):
    root = container.registry.get("local_folder").root
    (root / filename).write_text(text)


async def test_claim_dedup_key_is_atomic_under_concurrency(container):
    """Direct proof of the Phase 0 fix: dedup_seen()+dedup_mark() as two
    separate statements let two concurrent workers both pass the check
    before either marked it. claim_dedup_key() is one atomic round-trip —
    exactly one of N concurrent claimants may win. Real concurrency here
    means N asyncio tasks sharing the connection pool, not just N sequential
    calls — each task gets its own pooled connection and races for real."""
    key = "concurrent-dedup-key"

    results = await asyncio.gather(
        *(container.storage.claim_dedup_key(key, "role-x", "content") for _ in range(20))
    )

    assert results.count(True) == 1
    assert results.count(False) == 19


async def test_concurrent_ingestion_of_same_cv_creates_one_candidate(container):
    """N workers all pull() the same folder at once (e.g. a scheduled sync
    overlapping a manual "Pull now" click) — the same CV must not be
    ingested twice just because two pulls raced each other."""
    role = Role(id="role-conc-cv")
    await container.storage.upsert_role(role)
    _write_local(container, "same_cv.txt", CV_TEXT)

    await asyncio.gather(*(container.ingestion.ingest_for_role(role, "local_folder") for _ in range(10)))

    candidates = await container.storage.list_candidates(role.id)
    assert len(candidates) == 1


async def test_concurrent_proceed_replies_apply_exactly_once(container):
    """Two copies of the same reply racing the same open checkpoint (a
    redelivered webhook, or a duplicate pull) must not both flip the role
    and both send candidate_ack — exactly one may apply."""
    role = await _shortlisted_role_with_candidates(container, "role-conc-approve")
    await container.screening.run(role)
    item = InboundItem(
        source="gmail", item_id="reply-conc", name="reply-conc",
        sender=role.client_contact, body_text="PROCEED",
    )

    outcomes = await asyncio.gather(*(container.approval.process_inbound_item(item) for _ in range(10)))

    assert sum(1 for o in outcomes if o.applied) == 1
    stored = await container.storage.get_role(role.id)
    assert stored.status == RoleStatus.INTERVIEWING

    audit = await container.storage.list_audit(role.id)
    ack_sends = [a for a in audit if a.alert_type == "candidate_ack" and a.status == "sent"]
    assert len(ack_sends) == 2  # one ack per shortlisted candidate, not per racing reply


async def test_concurrent_approve_report_only_processes_once(container, client, run_worker_burst):
    """A double-click "approve" (or the same trigger fired twice) on the
    final report checkpoint must build/send the PDF exactly once. Since
    Phase 3, approve_report is queued: enqueuing itself always succeeds
    (202 x10, real concurrent HTTP requests via OS threads — TestClient.post()
    is blocking) but the atomic claim now lives in the job, which arq runs
    concurrently for real when the burst worker drains the queue. Exactly one
    of the 10 job results should show it actually sent the report."""
    role = Role(
        id="role-conc-report", title="Senior Backend Engineer",
        client_contact="client@clientco.example", status=RoleStatus.AWAITING_REPORT_APPROVAL,
    )
    await container.storage.upsert_role(role)

    def call(_):
        return client.post(f"/api/roles/{role.id}/approve_report")

    with ThreadPoolExecutor(max_workers=10) as ex:
        responses = list(ex.map(call, range(10)))

    assert all(r.status_code == 202 for r in responses)
    job_ids = [r.json()["job_id"] for r in responses]

    await run_worker_burst()

    results = [client.get(f"/api/jobs/{job_id}").json() for job_id in job_ids]
    assert all(r["status"] == "complete" for r in results)
    sent_outcomes = [r["result"]["sent"] for r in results]
    assert sent_outcomes.count(True) == 1
    assert sent_outcomes.count(False) == 9
