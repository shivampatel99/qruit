from __future__ import annotations

from sqlalchemy.ext.asyncio import create_async_engine

from app.models import InboundItem, Role
from tests.test_pipeline import _write_local


async def test_healthz_reports_db_unreachable_without_crashing(container, client):
    """Loop-doc §5: kill the DB connection mid-request. /healthz must report
    503 with a clear reason, not 500 or hang."""
    container.storage.engine = create_async_engine(
        "postgresql+psycopg://qruit:qruit@localhost:1/does-not-exist", pool_pre_ping=False,
    )

    resp = client.get("/healthz")

    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "unhealthy"
    assert any("database" in p for p in body["problems"])


async def test_healthz_reports_redis_unreachable_without_crashing(container, client):
    """Same as above for Redis — a dead queue backend must be visible on
    /healthz, not just discovered when a job silently never runs."""
    container.settings.redis_url = "redis://localhost:1/0"

    resp = client.get("/healthz")

    assert resp.status_code == 503
    assert any("redis" in p for p in resp.json()["problems"])


async def test_corrupted_pdf_does_not_crash_ingestion(container):
    """Loop-doc §5/§3 (local folder): a truncated/corrupt file fetched from
    any connector must be recorded as a per-item error, never crash the
    ingestion job or block other items in the same pull."""
    role = Role(id="role-chaos-corrupt-pdf")
    await container.storage.upsert_role(role)
    root = container.registry.get("local_folder").root
    (root / "cv_corrupt.pdf").write_bytes(b"%PDF-1.4 not actually a pdf, just garbage bytes")
    _write_local(container, "cv_good.txt", "Curriculum Vitae\nProfessional Experience: 5 years.\nSkills: python\ngood@example.com\n")

    result = await container.ingestion.ingest_for_role(role, "local_folder")

    assert any("cv_corrupt.pdf" in e for e in result.errors)
    assert result.candidates_added  # the good CV in the same pull still went through
    candidates = await container.storage.list_candidates(role.id)
    assert len(candidates) == 1


async def test_corrupt_image_ocr_failure_does_not_crash_ingestion(container):
    """Same guarantee for an image CV that fails OCR (e.g. not a real
    image): `_ocr_image` swallows the decode error and returns "" (see
    app/services/documents.py), so ingestion must complete — with an
    empty-text candidate, not an unhandled exception — rather than crash."""
    role = Role(id="role-chaos-corrupt-image")
    await container.storage.upsert_role(role)
    root = container.registry.get("local_folder").root
    (root / "cv_corrupt.png").write_bytes(b"not a real png")

    result = await container.ingestion.ingest_for_role(role, "local_folder")  # must not raise

    assert not result.errors
