from __future__ import annotations

from app.config import Settings
from app.crypto import ApprovalTokenSigner
from app.models import Role, RoleStatus, utcnow
from app.services.alerts import AlertType, DeliveryService
from app.services.reqruit_client import ReqruitInterviewProvider
from app.storage import Storage


class InterviewPipeline:
    """Module 3: starts interviews for shortlisted, non-skipped candidates,
    polls Reqruit.ai for completion, and fetches the report per candidate."""

    def __init__(
        self, settings: Settings, storage: Storage, interview_provider: ReqruitInterviewProvider,
        delivery: DeliveryService, signer: ApprovalTokenSigner,
    ):
        self.settings = settings
        self.storage = storage
        self.interview_provider = interview_provider
        self.delivery = delivery
        self.signer = signer

    async def start_interviews(self, role: Role) -> list[str]:
        started = []
        for candidate in await self.storage.list_candidates(role.id):
            if not candidate.shortlisted or candidate.skip_interview or candidate.interview_session_id:
                continue
            base = self.settings.qruit_public_base_url
            session_id = await self.interview_provider.start(
                jd_url=f"{base}/public/files/{role.jd_file_token}",
                resume_url=f"{base}/public/files/{candidate.cv_ref}",
                deep_screen_url=f"{base}/public/files/{candidate.deep_screen_file_token}",
            )
            candidate.interview_session_id = session_id
            candidate.interview_status = "invited"
            await self.storage.upsert_candidate(candidate)
            started.append(candidate.id)

            recipient = candidate.email or candidate.phone
            if recipient:
                token = self.signer.issue(role.id, AlertType.INTERVIEW_INVITE.value)
                await self.delivery.send_alert(
                    role, AlertType.INTERVIEW_INVITE, recipient,
                    {
                        "role_title": role.title or role.id,
                        "candidate_name": candidate.name or "there",
                        "interview_link": f"{base}/interview/{session_id}",
                    },
                    approval_token=token,
                )
        return started

    async def send_followup(self, role: Role, candidate_id: str, alert_type: AlertType) -> None:
        """Sends interview_reminder or interview_nudge; a no-op for a
        candidate whose interview was skipped (IV-5)."""
        candidates = await self.storage.list_candidates(role.id)
        candidate = next((c for c in candidates if c.id == candidate_id), None)
        if candidate is None or candidate.skip_interview or not candidate.interview_session_id:
            return
        if candidate.interview_status == "completed":
            return
        recipient = candidate.email or candidate.phone
        if not recipient:
            return
        token = self.signer.issue(role.id, alert_type.value)
        await self.delivery.send_alert(
            role, alert_type,
            recipient,
            {
                "role_title": role.title or role.id,
                "candidate_name": candidate.name or "there",
                "interview_link": f"{self.settings.qruit_public_base_url}/interview/{candidate.interview_session_id}",
            },
            approval_token=token,
        )

    async def poll_and_advance(self, role: Role) -> bool:
        """Polls every in-flight session; flips the role to
        awaiting-report-approval once every shortlisted candidate has either
        completed or been marked skipped. Returns whether the role advanced."""
        candidates = await self.storage.list_candidates(role.id)
        shortlisted = [c for c in candidates if c.shortlisted]
        for candidate in shortlisted:
            if candidate.skip_interview or candidate.interview_status == "completed":
                continue
            if not candidate.interview_session_id:
                continue
            status = await self.interview_provider.session_status(candidate.interview_session_id)
            if status == "completed":
                report = await self.interview_provider.fetch_report(candidate.interview_session_id)
                candidate.extracted["interview_report"] = report
                candidate.interview_status = "completed"
                await self.storage.upsert_candidate(candidate)
            else:
                candidate.interview_status = status
                await self.storage.upsert_candidate(candidate)

        all_done = all(
            c.skip_interview or c.interview_status == "completed" for c in shortlisted
        )
        if shortlisted and all_done and role.status != RoleStatus.AWAITING_REPORT_APPROVAL:
            role.status = RoleStatus.AWAITING_REPORT_APPROVAL
            role.updated_at = utcnow()
            await self.storage.upsert_role(role)
            return True
        return False
