from __future__ import annotations

from app.models import Candidate, InboundItem, Role, RoleStatus

JD_COMPLETE = (
    "Job Description\nSenior Backend Engineer\n"
    "Salary: $120k - $140k\nLocation: London, UK\nSeniority: senior\n"
    "Requirements: python, aws, docker\n"
)

JD_MISSING_SALARY = (
    "Job Description\nSenior Backend Engineer\n"
    "Location: London, UK\nSeniority: senior\nRequirements: python, aws\n"
)

CV_TEXT = (
    "Jane Roe\nCurriculum Vitae\nProfessional Experience: 6 years building backend systems.\n"
    "Skills: python, aws, docker\njane.roe@example.com\n"
)


def _write_local(container, filename: str, text: str):
    root = container.registry.get("local_folder").root
    (root / filename).write_text(text)


async def test_jd1_complete_jd_is_classified_and_extracted(container):
    role = Role(id="role-jd1", title="Senior Backend Engineer")
    await container.storage.upsert_role(role)
    _write_local(container, "role_jd.txt", JD_COMPLETE)

    result = await container.ingestion.ingest_for_role(role, "local_folder")

    assert result.jd_updated is True
    stored = await container.storage.get_role(role.id)
    assert stored.jd_data["salary_band"]
    assert stored.jd_data["location"]
    assert stored.jd_data["seniority"]


async def test_jd2_missing_fields_triggers_gap_question(container):
    role = Role(id="role-jd2", title="Senior Backend Engineer", client_contact="client@clientco.example")
    await container.storage.upsert_role(role)
    _write_local(container, "role_jd.txt", JD_MISSING_SALARY)

    await container.ingestion.ingest_for_role(role, "local_folder")

    stored = await container.storage.get_role(role.id)
    assert stored.status == RoleStatus.AWAITING_GAP_REPLY
    audit = await container.storage.list_audit(role.id)
    assert any(a.alert_type == "gap_question" and a.status == "sent" for a in audit)


async def test_jd3_complete_jd_does_not_send_gap_question(container):
    role = Role(id="role-jd3", title="Senior Backend Engineer", client_contact="client@clientco.example")
    await container.storage.upsert_role(role)
    _write_local(container, "role_jd.txt", JD_COMPLETE)

    await container.ingestion.ingest_for_role(role, "local_folder")

    stored = await container.storage.get_role(role.id)
    assert stored.status == RoleStatus.CV_INTAKE
    audit = await container.storage.list_audit(role.id)
    assert not any(a.alert_type == "gap_question" for a in audit)


async def test_cv1_cv_attachment_classified_and_linked_to_role(container):
    role = Role(id="role-cv1")
    await container.storage.upsert_role(role)
    _write_local(container, "candidate_cv.txt", CV_TEXT)

    result = await container.ingestion.ingest_for_role(role, "local_folder")

    assert len(result.candidates_added) == 1
    candidates = await container.storage.list_candidates(role.id)
    assert candidates[0].email == "jane.roe@example.com"
    assert candidates[0].role_id == role.id


async def test_cv3_same_cv_from_two_sources_deduplicated(container):
    role = Role(id="role-cv3")
    await container.storage.upsert_role(role)
    # gmail's mock fetch() always returns this exact byte content for any item.
    _write_local(container, "duplicate_cv.txt", "mock gmail attachment content")

    await container.ingestion.ingest_for_role(role, "local_folder")
    second = await container.ingestion.ingest_for_role(role, "gmail")

    candidates = await container.storage.list_candidates(role.id)
    # the gmail mock's CV item has byte-identical fetched content to the local
    # duplicate, so it must be screened once, not twice, even though it came
    # in through a different connector.
    assert len(candidates) == 1
    assert "mock-gmail-2" in second.skipped_duplicates


async def test_cv4_unrecognized_file_does_not_block_pipeline(container):
    role = Role(id="role-cv4")
    await container.storage.upsert_role(role)
    _write_local(container, "random_notes.txt", "just some unrelated notes about lunch plans")

    result = await container.ingestion.ingest_for_role(role, "local_folder")

    assert "random_notes.txt" in result.unrecognized
    assert result.errors == []


async def _shortlisted_role_with_candidates(container, role_id, client_contact="client@clientco.example"):
    role = Role(id=role_id, title="Senior Backend Engineer", client_contact=client_contact)
    role.jd_data = {"must_have_skills": ["python", "aws"]}
    await container.storage.upsert_role(role)
    for i in range(2):
        candidate = Candidate(
            id=f"{role_id}-cand-{i}", role_id=role_id, name=f"Candidate {i}",
            email=f"candidate{i}@example.com", extracted={"skills": ["python", "aws"]},
        )
        await container.storage.upsert_candidate(candidate)
    return role


async def test_sc1_screening_assembles_shortlist_and_sends_checkpoint(container):
    role = await _shortlisted_role_with_candidates(container, "role-sc1")
    shortlisted = await container.screening.run(role)
    assert len(shortlisted) == 2
    audit = await container.storage.list_audit(role.id)
    assert any(a.alert_type == "checkpoint_request" and a.status == "sent" for a in audit)
    checkpoint = await container.storage.latest_unresolved_token(role.id, "screening_approval")
    assert checkpoint is not None


async def test_sc2_proceed_advances_role(container):
    role = await _shortlisted_role_with_candidates(container, "role-sc2")
    await container.screening.run(role)

    item = InboundItem(source="gmail", item_id="reply-1", name="reply-1", sender=role.client_contact, body_text="PROCEED")
    outcome = await container.approval.process_inbound_item(item)

    assert outcome.applied is True
    stored = await container.storage.get_role(role.id)
    assert stored.status == RoleStatus.INTERVIEWING
    assert await container.storage.latest_unresolved_token(role.id, "screening_approval") is None


async def test_sc3_pause_keeps_role_paused(container):
    role = await _shortlisted_role_with_candidates(container, "role-sc3")
    await container.screening.run(role)

    item = InboundItem(source="gmail", item_id="reply-2", name="reply-2", sender=role.client_contact, body_text="pause")
    outcome = await container.approval.process_inbound_item(item)

    assert outcome.applied is True
    stored = await container.storage.get_role(role.id)
    assert stored.status == RoleStatus.PAUSED


async def test_sc5_skip_interview_bypasses_module3(container):
    role = await _shortlisted_role_with_candidates(container, "role-sc5")
    await container.screening.run(role)

    item = InboundItem(source="gmail", item_id="reply-3", name="reply-3", sender=role.client_contact, body_text="SKIP INTERVIEW")
    await container.approval.process_inbound_item(item)

    stored = await container.storage.get_role(role.id)
    assert stored.status == RoleStatus.AWAITING_REPORT_APPROVAL
    candidates = await container.storage.list_candidates(role.id)
    assert all(c.skip_interview for c in candidates)


async def test_sc6_reply_from_unregistered_address_ignored(container):
    role = await _shortlisted_role_with_candidates(container, "role-sc6")
    await container.screening.run(role)

    item = InboundItem(source="gmail", item_id="reply-4", name="reply-4", sender="stranger@nowhere.example", body_text="PROCEED")
    outcome = await container.approval.process_inbound_item(item)

    assert outcome.applied is False
    stored = await container.storage.get_role(role.id)
    assert stored.status == RoleStatus.AWAITING_SCREENING_APPROVAL


async def test_sc7_unrecognized_text_does_not_change_state(container):
    role = await _shortlisted_role_with_candidates(container, "role-sc7")
    await container.screening.run(role)

    item = InboundItem(source="gmail", item_id="reply-5", name="reply-5", sender=role.client_contact, body_text="sounds good, thanks!")
    outcome = await container.approval.process_inbound_item(item)

    assert outcome.applied is False
    stored = await container.storage.get_role(role.id)
    assert stored.status == RoleStatus.AWAITING_SCREENING_APPROVAL


async def test_al5_late_reply_after_role_already_proceeded_is_ignored(container):
    role = await _shortlisted_role_with_candidates(container, "role-al5")
    await container.screening.run(role)
    first = InboundItem(source="gmail", item_id="reply-6", name="reply-6", sender=role.client_contact, body_text="PROCEED")
    await container.approval.process_inbound_item(first)

    late = InboundItem(source="gmail", item_id="reply-7", name="reply-7", sender=role.client_contact, body_text="PAUSE")
    outcome = await container.approval.process_inbound_item(late)

    assert outcome.applied is False
    stored = await container.storage.get_role(role.id)
    assert stored.status == RoleStatus.INTERVIEWING
