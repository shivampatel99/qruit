from __future__ import annotations

import re
import uuid

import httpx
import redis

from app.config import Settings
from app.services.resilience import CircuitBreaker, with_backoff

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PHONE_RE = re.compile(r"(?:\+?\d[\d\s().-]{7,}\d)")
SALARY_RE = re.compile(r"(?:salary|compensation)[:\s]*([\$£€]?[\d,]+k?(?:\s*-\s*[\$£€]?[\d,]+k?)?)", re.I)
LOCATION_RE = re.compile(r"location[:\s]*([A-Za-z ,.-]+)", re.I)
SENIORITY_KEYWORDS = ["senior", "junior", "lead", "principal", "mid-level", "staff"]
SKILL_HINTS = [
    "python", "java", "javascript", "typescript", "react", "sql", "aws", "docker",
    "kubernetes", "go", "rust", "node", "django", "fastapi", "product management",
]


class ReqruitAuthError(Exception):
    pass


class ReqruitClient:
    """Wraps Reqruit.ai's extraction/deep-screening REST APIs (RQ-1).

    Inputs are passed as tokenised URLs the public host serves, never as raw
    bytes (FRD §5.4) — QRUIT only ever hands Reqruit.ai a `file_url`.
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self._breaker = CircuitBreaker(redis.Redis.from_url(settings.redis_url), service="reqruit")

    def _mock(self) -> bool:
        return self.settings.qruit_mock_api

    def _client(self) -> httpx.AsyncClient:
        if not self.settings.reqruit_auth_token:
            raise ReqruitAuthError("REQRUIT_AUTH_TOKEN not configured")
        return httpx.AsyncClient(
            base_url=self.settings.reqruit_base_url,
            headers={"Authorization": f"Bearer {self.settings.reqruit_auth_token}"},
            timeout=60.0,
        )

    @with_backoff()
    async def _post(self, client: httpx.AsyncClient, url: str, **kwargs) -> httpx.Response:
        """Guarded by a circuit breaker (loop-doc §1.2): once Reqruit.ai has
        failed `threshold` calls in a row, further calls fail instantly for
        `cooldown_seconds` instead of piling up retries against a dead
        provider. `with_backoff()` still retries transient 429/5xx within
        one call; the breaker is what stops it across calls."""
        with self._breaker.guard():
            resp = await client.post(url, **kwargs)
            self._raise_for_auth(resp)
            resp.raise_for_status()
            return resp

    async def extract_jd(self, file_url: str, text: str = "") -> dict:
        if self._mock():
            return self._mock_extract_jd(text)
        async with self._client() as client:
            resp = await self._post(client, "/jd/extract", json={"file_url": file_url})
            return resp.json()

    async def extract_resume(self, file_url: str, text: str = "") -> dict:
        if self._mock():
            return self._mock_extract_resume(text)
        async with self._client() as client:
            resp = await self._post(client, "/resume/extract", json={"file_url": file_url})
            return resp.json()

    async def run_deep_screening(self, jd: dict, resumes: list[dict], weights: dict | None = None) -> list[dict]:
        if self._mock():
            return self._mock_deep_screening(jd, resumes, weights or {})
        async with self._client() as client:
            resp = await self._post(
                client, "/deep-screening/run",
                json={"jd": jd, "resumes": resumes, "weights": weights or {}},
            )
            return resp.json().get("results", [])

    def _raise_for_auth(self, resp: httpx.Response) -> None:
        if resp.status_code in (401, 403):
            raise ReqruitAuthError(f"Reqruit.ai auth rejected the request: {resp.status_code}")

    # -- mock implementations --------------------------------------------

    def _mock_extract_jd(self, text: str) -> dict:
        salary = SALARY_RE.search(text)
        location = LOCATION_RE.search(text)
        seniority = next((k for k in SENIORITY_KEYWORDS if k in text.lower()), "")
        skills = [s for s in SKILL_HINTS if s in text.lower()]
        title_line = next((line.strip() for line in text.splitlines() if line.strip()), "Untitled Role")
        return {
            "role_title": title_line[:120],
            "seniority": seniority,
            "location": location.group(1).strip() if location else "",
            "salary_band": salary.group(1).strip() if salary else "",
            "must_have_skills": skills,
        }

    def _mock_extract_resume(self, text: str) -> dict:
        email = EMAIL_RE.search(text)
        phone = PHONE_RE.search(text)
        skills = [s for s in SKILL_HINTS if s in text.lower()]
        name_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
        return {
            "name": name_line[:80],
            "email": email.group(0) if email else "",
            "phone": phone.group(0) if phone else "",
            "skills": skills,
        }

    def _mock_deep_screening(self, jd: dict, resumes: list[dict], weights: dict) -> list[dict]:
        jd_skills = set(jd.get("must_have_skills", []))
        skill_weight = weights.get("skills", 1.0)
        results = []
        for resume in resumes:
            candidate_skills = set(resume.get("skills", []))
            overlap = len(jd_skills & candidate_skills)
            denom = max(len(jd_skills), 1)
            score = round(min(100.0, (overlap / denom) * 100 * skill_weight), 1)
            results.append({"resume_id": resume.get("id", ""), "score": score})
        return results


class ReqruitInterviewProvider:
    """Drives Module 3 via Reqruit.ai's interview API (RQ-2)."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._breaker = CircuitBreaker(redis.Redis.from_url(settings.redis_url), service="reqruit-interview")

    def _mock(self) -> bool:
        return self.settings.qruit_interview_mock

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self.settings.interview_base_url, timeout=60.0)

    @with_backoff()
    async def _post(self, client: httpx.AsyncClient, url: str, **kwargs) -> httpx.Response:
        with self._breaker.guard():
            resp = await client.post(url, **kwargs)
            resp.raise_for_status()
            return resp

    @with_backoff()
    async def _get(self, client: httpx.AsyncClient, url: str, **kwargs) -> httpx.Response:
        with self._breaker.guard():
            resp = await client.get(url, **kwargs)
            resp.raise_for_status()
            return resp

    async def start(self, jd_url: str, resume_url: str, deep_screen_url: str) -> str:
        if self._mock():
            return f"mock-session-{uuid.uuid4().hex[:12]}"
        async with self._client() as client:
            resp = await self._post(
                client, "/v2/interview/start",
                json={"jd_source": jd_url, "resume_source": resume_url, "deep_screen_source": deep_screen_url},
            )
            return resp.json()["session_id"]

    async def next(self, session_id: str, message: str = "") -> dict:
        """Drives one turn of the interview (FRD Stage 5.4). The candidate-
        facing page calls this with an empty message to get the opening
        prompt, then with each answer in turn."""
        if self._mock():
            if not message:
                return {"status": "in_progress", "prompt": "Tell me about a project you're proud of."}
            return {"status": "completed", "prompt": "Thanks — that's the last question. Submitting your interview now."}
        async with self._client() as client:
            resp = await self._post(client, "/v2/interview/next", json={"session_id": session_id, "message": message})
            return resp.json()

    async def session_status(self, session_id: str) -> str:
        if self._mock():
            return "completed" if session_id.startswith("mock-session-") else "in_progress"
        async with self._client() as client:
            resp = await self._get(client, f"/v2/interview/session/{session_id}")
            return resp.json().get("status", "unknown")

    async def fetch_report(self, session_id: str) -> dict:
        if self._mock():
            return {
                "session_id": session_id,
                "summary": "Mock interview report: candidate answered all turns satisfactorily.",
                "recommendation": "advance",
                "scores": {"communication": 82, "technical": 78},
            }
        async with self._client() as client:
            resp = await self._get(client, f"/v2/interview/session/{session_id}/report")
            return resp.json()
