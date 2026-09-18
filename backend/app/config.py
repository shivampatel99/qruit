from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_ROOT / ".env", extra="ignore")

    qruit_approval_secret: str = "dev-only-insecure-secret-change-me"
    qruit_public_base_url: str = "http://localhost:8787"
    # Comma-separated list of origins allowed to call the API from a browser
    # (the frontend's URL). Dev default matches `npm run dev`'s Vite port.
    cors_allowed_origins: str = "http://localhost:5173"
    qruit_data_dir: Path = BACKEND_ROOT / "data"

    qruit_email_mock: bool = True
    qruit_mock_api: bool = True
    qruit_interview_mock: bool = True
    whatsapp_mock: bool = True

    google_client_id: str = ""
    google_client_secret: str = ""
    google_drive_folder_id: str = ""

    ms_client_id: str = ""
    ms_client_secret: str = ""
    ms_tenant: str = ""
    sharepoint_site_id: str = ""
    sharepoint_folder_path: str = ""

    whatsapp_token: str = ""
    whatsapp_phone_id: str = ""
    whatsapp_verify_token: str = "dev-verify-token"
    whatsapp_app_secret: str = ""

    reqruit_auth_token: str = ""
    reqruit_base_url: str = "https://deep-screening.reqruit.ai"
    interview_base_url: str = "https://interview.reqruit.ai"

    default_sla_hours: int = 48

    database_url: str = "postgresql+psycopg://qruit:qruit_dev_password@localhost:5432/qruit"
    redis_url: str = "redis://localhost:6379/0"
    arq_queue_name: str = "qruit:jobs"

    def validate_for_boot(self) -> None:
        """Fails fast at startup with the exact missing var(s) instead of a
        connector silently misbehaving three requests later (loop-doc §1.10)."""
        missing: list[str] = []
        if not self.qruit_email_mock:
            has_google = bool(self.google_client_id and self.google_client_secret)
            has_microsoft = bool(self.ms_client_id and self.ms_client_secret and self.ms_tenant)
            if not (has_google or has_microsoft):
                missing.append(
                    "QRUIT_EMAIL_MOCK=false requires GOOGLE_CLIENT_ID+GOOGLE_CLIENT_SECRET "
                    "or MS_CLIENT_ID+MS_CLIENT_SECRET+MS_TENANT"
                )
        if not self.whatsapp_mock and not (self.whatsapp_token and self.whatsapp_phone_id):
            missing.append("WHATSAPP_MOCK=false requires WHATSAPP_TOKEN and WHATSAPP_PHONE_ID")
        if not self.qruit_mock_api and not self.reqruit_auth_token:
            missing.append("QRUIT_MOCK_API=false requires REQRUIT_AUTH_TOKEN")
        if self.qruit_approval_secret == "dev-only-insecure-secret-change-me" and not self.qruit_email_mock:
            missing.append("QRUIT_APPROVAL_SECRET must be set to a real secret outside mock mode")
        if missing:
            raise RuntimeError("QRUIT config is incomplete:\n- " + "\n- ".join(missing))

    @property
    def outbox_dir(self) -> Path:
        return self.qruit_data_dir / "outbox"

    @property
    def files_dir(self) -> Path:
        return self.qruit_data_dir / "files"


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.qruit_data_dir.mkdir(parents=True, exist_ok=True)
    settings.outbox_dir.mkdir(parents=True, exist_ok=True)
    settings.files_dir.mkdir(parents=True, exist_ok=True)
    return settings
