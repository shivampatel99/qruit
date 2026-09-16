from __future__ import annotations

from arq.connections import RedisSettings

from app.bootstrap import build_container
from app.config import Settings, get_settings
from app.models import RoleStatus
from app.services import report as report_service
from app.services.alerts import AlertType

# Tests point the worker at their own ephemeral Postgres/Redis by seeding the
# Worker's ctx with {"settings": <test Settings>} at construction time (see
# tests/conftest.py) — startup() prefers that over the real process settings.


async def startup(ctx: dict) -> None:
    settings: Settings = ctx.get("settings") or get_settings()
    ctx["container"] = await build_container(settings)


async def shutdown(ctx: dict) -> None:
    container = ctx.get("container")
    if container is not None:
        await container.storage.engine.dispose()


async def ping(ctx: dict) -> str:
    """Smoke-test job proving the worker boots and can execute a job."""
    return "pong"


async def pull_for_role(
    ctx: dict, role_id: str, connector_name: str, query: str = "", folder: str = "", limit: int = 25,
) -> dict:
    container = ctx["container"]
    role = await container.storage.get_role(role_id)
    if role is None:
        raise ValueError(f"role {role_id} not found")
    result = await container.ingestion.ingest_for_role(
        role, connector_name, query=query, folder=folder, limit=limit,
    )
    return {
        "jd_updated": result.jd_updated, "candidates_added": result.candidates_added,
        "unrecognized": result.unrecognized, "skipped_duplicates": result.skipped_duplicates,
        "errors": result.errors,
    }


async def run_screening(ctx: dict, role_id: str) -> dict:
    container = ctx["container"]
    role = await container.storage.get_role(role_id)
    if role is None:
        raise ValueError(f"role {role_id} not found")
    shortlisted = await container.screening.run(role)
    return {"shortlisted_candidate_ids": shortlisted}


async def check_replies(ctx: dict, role_id: str, connector_name: str, query: str = "", limit: int = 25) -> dict:
    container = ctx["container"]
    role = await container.storage.get_role(role_id)
    if role is None:
        raise ValueError(f"role {role_id} not found")
    connector = container.registry.get(connector_name)
    items = await connector.pull(query=query, limit=limit)
    outcomes = []
    for item in items:
        outcome = await container.approval.process_inbound_item(item)
        outcomes.append({"applied": outcome.applied, "reason": outcome.reason})
    return {"processed": outcomes}


async def start_interviews(ctx: dict, role_id: str) -> dict:
    container = ctx["container"]
    role = await container.storage.get_role(role_id)
    if role is None:
        raise ValueError(f"role {role_id} not found")
    started = await container.interview.start_interviews(role)
    return {"started_candidate_ids": started}


async def poll_interviews(ctx: dict, role_id: str) -> dict:
    container = ctx["container"]
    role = await container.storage.get_role(role_id)
    if role is None:
        raise ValueError(f"role {role_id} not found")
    advanced = await container.interview.poll_and_advance(role)
    return {"advanced_to_report_approval": advanced}


async def approve_report(ctx: dict, role_id: str) -> dict:
    """The Module 4 checkpoint job: builds the PDF and sends report_email
    only once — the atomic status claim below is what makes a duplicate
    enqueue of the same approval a no-op instead of a double-send (RP-3,
    same guarantee the synchronous endpoint used to provide directly)."""
    container = ctx["container"]
    role = await container.storage.get_role(role_id)
    if role is None:
        raise ValueError(f"role {role_id} not found")

    claimed = await container.storage.compare_and_swap_role_status(
        role_id, RoleStatus.AWAITING_REPORT_APPROVAL, RoleStatus.CLOSED,
    )
    if not claimed:
        return {"sent": False, "error": "role is not awaiting final report approval, or is already being processed"}

    candidates = await container.storage.list_candidates(role.id)
    dest = str(container.settings.qruit_data_dir / "reports" / f"{role.id}.pdf")
    report_service.build_recommendation_report(role, candidates, dest)

    token = container.signer.issue(role.id, AlertType.REPORT_EMAIL.value)
    outcome = await container.delivery.send_alert(
        role, AlertType.REPORT_EMAIL, role.client_contact,
        {"role_title": role.title or role.id}, approval_token=token, attachments=[dest],
    )
    if not outcome.sent:
        # Revert the claim so a retry is possible — the role isn't really closed.
        await container.storage.compare_and_swap_role_status(
            role_id, RoleStatus.CLOSED, RoleStatus.AWAITING_REPORT_APPROVAL,
        )
    return {"sent": outcome.sent, "error": outcome.error, "report_path": dest}


class WorkerSettings:
    functions = [
        ping, pull_for_role, run_screening, check_replies,
        start_interviews, poll_interviews, approve_report,
    ]
    queue_name = get_settings().arq_queue_name
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
