from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]

# Values that are published in docs or committed in this repo. Never acceptable outside local dev.
SUPABASE_LOCAL_JWT_SECRET = "super-secret-jwt-token-with-at-least-32-characters-long"
PLACEHOLDER_SECRETS = {"", "change-me", "changeme", "secret", "..."}
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0", "host.docker.internal"}


def _is_local(url: str) -> bool:
    return (urlparse(url).hostname or "") in LOCAL_HOSTS


class InsecureConfiguration(RuntimeError):
    pass


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env", ".env"), extra="ignore", env_file_encoding="utf-8"
    )

    # "production" turns the startup security check from warnings into a hard failure. "auto" (default)
    # treats the app as production as soon as it is exposed: Twilio on, or non-local public/Supabase URLs.
    app_env: Literal["auto", "development", "production"] = "auto"
    log_level: str = "INFO"              # DEBUG makes Pipecat log full prompts (caller PII); never in production
    enable_api_docs: bool | None = None  # /docs and /openapi.json; default on in development only
    retention_days: int = 365            # transcripts and recordings older than this are purged
    database_url: str = "postgresql://postgres:postgres@127.0.0.1:54322/postgres"
    supabase_url: str = "http://127.0.0.1:54321"
    supabase_jwt_secret: str = SUPABASE_LOCAL_JWT_SECRET
    # JWT app_metadata.role required for dashboard API access (matches public.is_admin() in SQL).
    admin_role: str = "admin"

    # Leave the keys empty to use the AWS CLI's credential chain (AWS_PROFILE or the default profile).
    aws_profile: str = ""
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    aws_session_token: str = ""
    aws_region: str = "us-east-1"
    nova_sonic_model_id: str = "amazon.nova-2-5-sonic"
    bedrock_claude_model_id: str = "anthropic.claude-opus-5-5"

    telephony_provider: Literal["twilio", "webrtc_only"] = "webrtc_only"
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_phone_number: str = ""
    public_voice_webhook_base_url: str = "http://localhost:8000"
    validate_twilio_signature: bool = True
    validate_twilio_ws_signature: bool = True

    # Outbound dialing guard (toll fraud, harassment, TCPA-style limits).
    allowed_dial_country_codes: list[str] = ["1"]   # E.164 country codes; "1" = US/Canada (NANP)
    max_dials_per_number_per_day: int = 3
    max_dials_per_hour: int = 30
    require_dial_consent: bool = True

    # In-call abuse caps.
    max_availability_checks_per_call: int = 6
    max_callbacks_per_call: int = 2
    max_upcoming_appointments_per_contact: int = 1
    max_bookings_per_hour: int = 20          # all callers together; each booking emails an invite
    max_caller_field_chars: int = 200

    # API rate limits (per client, in-process).
    leads_per_minute: int = 30
    webrtc_offers_per_minute: int = 6
    max_concurrent_browser_calls: int = 3
    max_concurrent_calls: int = 10           # phone + browser; bounds Bedrock and Twilio spend

    cal_api_key: str = ""
    cal_api_base_url: str = "https://api.cal.com/v2"

    lead_webhook_secret: str = ""
    cors_origins: list[str] = ["http://localhost:3000", "http://127.0.0.1:3000"]
    enable_retry_worker: bool = True

    @property
    def is_production(self) -> bool:
        if self.app_env != "auto":
            return self.app_env == "production"
        return (
            self.telephony_provider == "twilio"
            or not _is_local(self.public_voice_webhook_base_url)
            or not _is_local(self.supabase_url)
        )

    @property
    def api_docs_enabled(self) -> bool:
        return (not self.is_production) if self.enable_api_docs is None else self.enable_api_docs

    def security_problems(self) -> list[str]:
        """Settings that are unsafe anywhere except a developer's own machine."""
        problems = []
        if self.log_level.upper() in ("DEBUG", "TRACE"):
            problems.append("LOG_LEVEL is DEBUG/TRACE, which logs full prompts with caller personal data.")
        if any(_is_local(o) is False and o.startswith("http://") for o in self.cors_origins):
            problems.append("CORS_ORIGINS contains a non-local http:// origin.")
        if self.supabase_jwt_secret == SUPABASE_LOCAL_JWT_SECRET or len(self.supabase_jwt_secret) < 32:
            problems.append("SUPABASE_JWT_SECRET is the public Supabase local-dev secret (or shorter than 32 chars).")
        if self.lead_webhook_secret and (self.lead_webhook_secret.lower() in PLACEHOLDER_SECRETS or len(self.lead_webhook_secret) < 24):
            problems.append("LEAD_WEBHOOK_SECRET is a placeholder or shorter than 24 chars.")
        if "postgres:postgres@" in self.database_url:
            problems.append("DATABASE_URL uses the default postgres:postgres credentials.")
        if self.telephony_provider == "twilio":
            if not (self.validate_twilio_signature and self.validate_twilio_ws_signature):
                problems.append("Twilio signature validation is turned off.")
            if not self.public_voice_webhook_base_url.startswith("https://"):
                problems.append("PUBLIC_VOICE_WEBHOOK_BASE_URL must be https:// when Twilio is enabled.")
        return problems

    def check_security(self) -> list[str]:
        """Fail closed in production; return the problems (to log as warnings) in development."""
        problems = self.security_problems()
        if problems and self.is_production:
            raise InsecureConfiguration(
                "Refusing to start in production (APP_ENV, or auto-detected because Twilio is on or the "
                "public/Supabase URLs are not local):\n- " + "\n- ".join(problems)
            )
        return problems

    def lead_webhook_enabled(self) -> bool:
        return bool(self.lead_webhook_secret) and self.lead_webhook_secret.lower() not in PLACEHOLDER_SECRETS

    @property
    def dsn(self) -> str:
        # asyncpg wants a plain postgresql:// DSN
        return self.database_url.replace("postgresql+asyncpg://", "postgresql://")

    @property
    def public_ws_url(self) -> str:
        base = self.public_voice_webhook_base_url.rstrip("/")
        return base.replace("https://", "wss://").replace("http://", "ws://") + "/ws/voice"


@lru_cache
def get_settings() -> Settings:
    return Settings()
