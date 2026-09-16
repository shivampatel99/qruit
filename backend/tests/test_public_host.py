from __future__ import annotations

from app.models import Candidate, Role

JD_COMPLETE = (
    "Job Description\nSenior Backend Engineer\n"
    "Salary: $120k - $140k\nLocation: London, UK\nSeniority: senior\n"
    "Requirements: python, aws, docker\n"
)

CV_TEXT = (
    "Jane Roe\nCurriculum Vitae\nProfessional Experience: 6 years building backend systems.\n"
    "Skills: python, aws, docker\njane.roe@example.com\n"
)


def _write_local(container, filename: str, text: str):
    root = container.registry.get("local_folder").root
    (root / filename).write_text(text)


async def test_public_file_round_trip(container, client, tmp_path):
    source = tmp_path / "sample.docx"
    source.write_text("hello reqruit")
    reg_token = await container.storage.register_public_file(str(source), content_type="text/plain")

    resp = client.get(f"/public/files/{reg_token}")
    assert resp.status_code == 200
    assert resp.text == "hello reqruit"


def test_public_file_unknown_token_is_404(client):
    resp = client.get("/public/files/does-not-exist")
    assert resp.status_code == 404


async def test_ingestion_publishes_real_jd_and_cv_tokens(container, client):
    role = Role(id="role-pub1", title="Senior Backend Engineer")
    await container.storage.upsert_role(role)
    _write_local(container, "role_jd.txt", JD_COMPLETE)
    await container.ingestion.ingest_for_role(role, "local_folder")

    stored = await container.storage.get_role(role.id)
    assert stored.jd_file_token
    resp = client.get(f"/public/files/{stored.jd_file_token}")
    assert resp.status_code == 200

    _write_local(container, "candidate_cv.txt", CV_TEXT)
    await container.ingestion.ingest_for_role(role, "local_folder")
    candidates = await container.storage.list_candidates(role.id)
    cv_candidate = next(c for c in candidates if c.email)
    assert cv_candidate.cv_ref
    resp = client.get(f"/public/files/{cv_candidate.cv_ref}")
    assert resp.status_code == 200


def test_oauth_callback_rejects_unknown_state(client):
    resp = client.get("/api/oauth/callback", params={"code": "abc", "state": "never-issued"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["connected"] is False
    assert "state" in body["error"]


def test_oauth_connect_issues_authorize_url_and_pending_state(container, client):
    resp = client.get("/api/oauth/gmail/connect")
    assert resp.status_code == 200
    authorize_url = resp.json()["authorize_url"]
    assert authorize_url.startswith("https://accounts.google.com/")
    assert "drive.readonly" in authorize_url  # GD-1: one consent covers Gmail + Drive


def test_oauth_callback_exchanges_code_and_stores_token(container, client, monkeypatch):
    connect_resp = client.get("/api/oauth/gmail/connect")
    authorize_url = connect_resp.json()["authorize_url"]
    from urllib.parse import parse_qs, urlparse

    state = parse_qs(urlparse(authorize_url).query)["state"][0]

    gmail = container.registry.get("gmail")
    from app.connectors.oauth_base import TokenBlob

    async def fake_exchange_code(code, redirect_uri, code_verifier):
        return TokenBlob(
            access_token="tok", refresh_token="rtok", expires_at=9999999999.0,
            scopes=gmail.required_scopes, account_label="recruiting@clientco.example",
        )

    monkeypatch.setattr(gmail, "exchange_code", fake_exchange_code)

    resp = client.get("/api/oauth/callback", params={"code": "real-code", "state": state})
    body = resp.json()
    assert body["connected"] is True
    assert body["account_label"] == "recruiting@clientco.example"

    # replaying the same state a second time must not succeed (single-use).
    replay = client.get("/api/oauth/callback", params={"code": "real-code", "state": state})
    assert replay.json()["connected"] is False


async def _interview_ready_role(container):
    role = Role(id="role-iv1", title="Senior Backend Engineer", client_contact="client@clientco.example")
    role.jd_data = {"must_have_skills": ["python"]}
    await container.storage.upsert_role(role)
    candidate = Candidate(
        id="cand-iv1", role_id=role.id, name="Jane Roe", email="jane@example.com",
        extracted={"skills": ["python"]}, shortlisted=True, cv_ref="cv-token-1",
    )
    await container.storage.upsert_candidate(candidate)
    role.jd_file_token = "jd-token-1"
    await container.storage.upsert_role(role)
    return role, candidate


async def test_interview_session_route_serves_prompt_and_records_reply(container, client):
    role, candidate = await _interview_ready_role(container)
    started = await container.interview.start_interviews(role)
    assert started == [candidate.id]

    stored_candidates = await container.storage.list_candidates(role.id)
    session_id = stored_candidates[0].interview_session_id
    assert session_id

    opened = client.get(f"/interview/{session_id}")
    assert opened.status_code == 200
    assert opened.json()["candidate_name"] == "Jane Roe"

    replied = client.post(f"/interview/{session_id}/respond", json={"message": "I led a migration project."})
    assert replied.status_code == 200
    assert replied.json()["status"] == "completed"


def test_interview_session_route_unknown_session_is_404(client):
    resp = client.get("/interview/does-not-exist")
    assert resp.status_code == 404


async def test_recruiter_notify_email_gets_a_copy_of_client_alerts(container):
    role = Role(
        id="role-notify1", title="Senior Backend Engineer",
        client_contact="client@clientco.example", notify_email="recruiter@ourfirm.example",
    )
    await container.storage.upsert_role(role)
    candidate = Candidate(
        id="cand-notify1", role_id=role.id, email="candidate@example.com",
        extracted={"skills": ["python"]},
    )
    await container.storage.upsert_candidate(candidate)

    await container.screening.run(role)

    audit = await container.storage.list_audit(role.id)
    assert any(a.alert_type == "checkpoint_request" and a.recipient == "client@clientco.example" for a in audit)
    assert any(a.alert_type == "checkpoint_request_copy" and a.recipient == "recruiter@ourfirm.example" for a in audit)
