from __future__ import annotations

import json

from app.config import Settings
from app.crypto import ApprovalTokenSigner
from app.models import ApprovalToken, Role, RoleStatus, utcnow
from app.services.alerts import AlertType, DeliveryService
from app.services.reqruit_client import ReqruitClient
from app.storage import Storage

SCREENING_CHECKPOINT = "screening_approval"
SHORTLIST_THRESHOLD = 60.0


class ScreeningPipeline:
    def __init__(
        self, settings: Settings, storage: Storage, reqruit: ReqruitClient,
        delivery: DeliveryService, signer: ApprovalTokenSigner,
    ):
        self.settings = settings
        self.storage = storage
        self.reqruit = reqruit
        self.delivery = delivery
        self.signer = signer

    async def run(self, role: Role) -> list[str]:
        """Scores every extracted CV for the role and opens the shortlist
        checkpoint (SC-1). Returns the ids of shortlisted candidates."""
        candidates = await self.storage.list_candidates(role.id)
        resumes = [{"id": c.id, "skills": c.extracted.get("skills", [])} for c in candidates]
        deep_screen_results = await self.reqruit.run_deep_screening(role.jd_data, resumes, role.screening_weights)
        scores = {r["resume_id"]: r["score"] for r in deep_screen_results}

        shortlisted_ids = []
        for candidate in candidates:
            candidate.score = scores.get(candidate.id, 0.0)
            candidate.shortlisted = candidate.score >= SHORTLIST_THRESHOLD
            candidate.deep_screen_file_token = await self._publish_deep_screen_result(
                role.id, candidate.id, candidate.score,
            )
            await self.storage.upsert_candidate(candidate)
            if candidate.shortlisted:
                shortlisted_ids.append(candidate.id)

        role.status = RoleStatus.AWAITING_SCREENING_APPROVAL
        role.updated_at = utcnow()
        await self.storage.upsert_role(role)

        checkpoint_token = self.signer.issue(role.id, SCREENING_CHECKPOINT)
        await self.storage.save_approval_token(
            ApprovalToken(token=checkpoint_token, role_id=role.id, checkpoint_type=SCREENING_CHECKPOINT)
        )

        summary = f"{len(shortlisted_ids)} of {len(candidates)} candidates shortlisted."
        outcome = await self.delivery.send_alert(
            role, AlertType.CHECKPOINT_REQUEST, role.client_contact,
            {"role_title": role.title or role.id, "summary": summary},
        )
        if not outcome.sent and role.client_contact:
            # SC-9: failure is recorded (via delivery's own audit call) and the
            # role must not silently proceed — it stays at the checkpoint.
            pass
        return shortlisted_ids

    async def _publish_deep_screen_result(self, role_id: str, candidate_id: str, score: float) -> str:
        """Reqruit.ai's interview API takes the deep-screen result as a
        tokenised URL alongside the JD and CV (RQ-2), same publishing scheme."""
        dest = self.settings.files_dir / role_id / f"deep-screen-{candidate_id}.json"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps({"candidate_id": candidate_id, "score": score}), encoding="utf-8")
        return await self.storage.register_public_file(str(dest), content_type="application/json")
