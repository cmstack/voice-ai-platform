import pytest

from src.config import get_settings
from src.services import aws


@pytest.fixture
def fake_aws_home(tmp_path, monkeypatch):
    creds = tmp_path / "credentials"
    creds.write_text(
        "[default]\naws_access_key_id = AKIADEFAULT\naws_secret_access_key = default-secret\n"
        "[voice]\naws_access_key_id = AKIAVOICE\naws_secret_access_key = voice-secret\naws_session_token = tok\n"
    )
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(creds))
    monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "config"))
    for var in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_PROFILE"):
        monkeypatch.delenv(var, raising=False)
    s = get_settings()
    monkeypatch.setattr(s, "aws_access_key_id", "")
    monkeypatch.setattr(s, "aws_secret_access_key", "")
    monkeypatch.setattr(s, "aws_profile", "")


def test_uses_default_cli_profile(fake_aws_home):
    c = aws.resolve_credentials()
    assert (c.access_key_id, c.secret_access_key, c.session_token, c.region) == ("AKIADEFAULT", "default-secret", None, "us-east-1")


def test_uses_named_profile(fake_aws_home, monkeypatch):
    monkeypatch.setattr(get_settings(), "aws_profile", "voice")
    c = aws.resolve_credentials()
    assert (c.access_key_id, c.session_token) == ("AKIAVOICE", "tok")


def test_explicit_env_keys_win(fake_aws_home, monkeypatch):
    monkeypatch.setattr(get_settings(), "aws_access_key_id", "AKIAENV")
    monkeypatch.setattr(get_settings(), "aws_secret_access_key", "env-secret")
    assert aws.resolve_credentials().access_key_id == "AKIAENV"


def test_missing_credentials_raise_helpful_error(tmp_path, monkeypatch, fake_aws_home):
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "none"))
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    with pytest.raises(aws.AWSCredentialsMissing, match="aws configure"):
        aws.resolve_credentials()
