from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env", ".env"), extra="ignore", env_file_encoding="utf-8"
    )

    database_url: str = "postgresql://postgres:postgres@127.0.0.1:54322/postgres"
    supabase_url: str = "http://127.0.0.1:54321"
    supabase_jwt_secret: str = "super-secret-jwt-token-with-at-least-32-characters-long"

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

    cal_api_key: str = ""
    cal_api_base_url: str = "https://api.cal.com/v2"

    lead_webhook_secret: str = ""
    cors_origins: list[str] = ["http://localhost:3000", "http://127.0.0.1:3000"]
    enable_retry_worker: bool = True

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
