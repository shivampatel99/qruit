from __future__ import annotations

from app.models import Candidate, Role


async def test_pull_endpoint_enqueues_and_job_completes(container, client, run_worker_burst):
    role = Role(id="role-job1")
    await container.storage.upsert_role(role)

    resp = client.post(f"/api/roles/{role.id}/pull", json={"connector": "gmail"})
    assert resp.status_code == 202
    job_id = resp.json()["job_id"]

    # Not processed yet — nothing has drained the queue.
    pending = client.get(f"/api/jobs/{job_id}")
    assert pending.json()["status"] in ("queued", "in_progress")

    await run_worker_burst()

    done = client.get(f"/api/jobs/{job_id}")
    body = done.json()
    assert body["status"] == "complete"
    assert body["result"]["candidates_added"] or body["result"]["unrecognized"]


async def test_run_screening_endpoint_round_trip(container, client, run_worker_burst):
    role = Role(id="role-job2", title="Senior Backend Engineer", client_contact="client@clientco.example")
    role.jd_data = {"must_have_skills": ["python"]}
    await container.storage.upsert_role(role)
    await container.storage.upsert_candidate(
        Candidate(id="cand-job2", role_id=role.id, email="a@example.com", extracted={"skills": ["python"]})
    )

    resp = client.post(f"/api/roles/{role.id}/run_screening")
    assert resp.status_code == 202
    job_id = resp.json()["job_id"]

    await run_worker_burst()

    result = client.get(f"/api/jobs/{job_id}").json()
    assert result["status"] == "complete"
    assert result["result"]["shortlisted_candidate_ids"] == ["cand-job2"]


def test_unknown_job_id_reports_not_found(client):
    resp = client.get("/api/jobs/does-not-exist")
    assert resp.status_code == 200
    assert resp.json()["status"] == "not_found"


async def test_queue_backlog_drains_completely_with_limited_worker_concurrency(container, client, run_worker_burst):
    """Loop-doc §4.3: push far more jobs than the worker can run at once and
    confirm they all still complete — no crash, no dropped jobs."""
    role = Role(id="role-backlog")
    await container.storage.upsert_role(role)

    job_ids = []
    for _ in range(30):
        resp = client.post(f"/api/roles/{role.id}/pull", json={"connector": "gmail"})
        assert resp.status_code == 202
        job_ids.append(resp.json()["job_id"])

    completed = await run_worker_burst(worker_concurrency=3)
    assert completed == 30

    statuses = [client.get(f"/api/jobs/{job_id}").json() for job_id in job_ids]
    assert all(s["status"] == "complete" for s in statuses)
