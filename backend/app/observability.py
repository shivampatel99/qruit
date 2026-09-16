from __future__ import annotations

import contextvars
import logging
import uuid

import structlog
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="")
job_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("job_id", default="")

# Never let a token/secret reach a log line, even by accident in a kwarg
# somewhere — this is an enforced processor, not a convention to remember.
REDACT_KEYS = {
    "token", "access_token", "refresh_token", "approval_token", "encrypted_token",
    "client_secret", "secret", "password", "authorization", "code_verifier",
}


def _redact_processor(logger, method_name, event_dict):
    for key in list(event_dict.keys()):
        if key.lower() in REDACT_KEYS:
            event_dict[key] = "***redacted***"
    return event_dict


def _add_ids_processor(logger, method_name, event_dict):
    request_id = request_id_var.get()
    job_id = job_id_var.get()
    if request_id:
        event_dict["request_id"] = request_id
    if job_id:
        event_dict["job_id"] = job_id
    return event_dict


def configure_logging() -> None:
    structlog.configure(
        processors=[
            _add_ids_processor,
            _redact_processor,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str = "qruit"):
    return structlog.get_logger(name)


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Assigns (or propagates) a request id for every log line emitted while
    handling this request, and echoes it back so a client can correlate."""

    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-ID", uuid.uuid4().hex)
        token = request_id_var.set(request_id)
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
        response.headers["X-Request-ID"] = request_id
        return response


class PublicHostnameGuardMiddleware(BaseHTTPMiddleware):
    """/api/* must never be reachable via the public hostname (FRD §5.6, NW-3
    — "QRUIT already rejects /api requests that arrive via the public host
    name"). Compares the inbound Host header against QRUIT_PUBLIC_BASE_URL's
    hostname; a reverse proxy forwarding the public name through unchanged is
    exactly the case this exists to catch.

    /api/oauth/callback is the one deliberate exception: the doc's own
    Configuration map registers it as the OAuth redirect URI at
    `{QRUIT_PUBLIC_BASE_URL}/api/oauth/callback`, so Google/Microsoft calling
    it back necessarily arrives via the public hostname. Same category as the
    WhatsApp webhook exception — narrow, named, not a precedent."""

    EXEMPT_PATHS = {"/api/oauth/callback"}

    def __init__(self, app, public_hostname: str):
        super().__init__(app)
        self.public_hostname = public_hostname.lower()

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if self.public_hostname and path.startswith("/api/") and path not in self.EXEMPT_PATHS:
            host = (request.headers.get("host") or "").split(":")[0].lower()
            if host == self.public_hostname:
                return Response(
                    content='{"detail":"/api/* is not reachable via the public hostname"}',
                    status_code=403, media_type="application/json",
                )
        return await call_next(request)


# -- metrics ----------------------------------------------------------------

CONNECTOR_CALL_LATENCY = Histogram(
    "qruit_connector_call_latency_seconds", "External connector call latency",
    ["connector", "operation"],
)
CONNECTOR_CALL_ERRORS = Counter(
    "qruit_connector_call_errors_total", "External connector call errors",
    ["connector", "operation"],
)
ALERT_SEND_RESULTS = Counter(
    "qruit_alert_send_total", "Alert send attempts by outcome", ["alert_type", "status"],
)
TOKEN_REFRESH_FAILURES = Counter(
    "qruit_token_refresh_failures_total", "OAuth token refresh failures", ["channel"],
)
QUEUE_DEPTH = Gauge("qruit_queue_depth", "Jobs currently queued", ["queue"])


def metrics_response() -> Response:
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
