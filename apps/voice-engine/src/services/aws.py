"""AWS credentials via the standard chain: explicit .env keys, else AWS_PROFILE / default profile
(~/.aws), SSO, or an instance/container role — whatever the AWS CLI itself would use."""

from dataclasses import dataclass

import boto3

from src.config import get_settings


class AWSCredentialsMissing(RuntimeError):
    pass


@dataclass(frozen=True)
class AWSCredentials:
    access_key_id: str
    secret_access_key: str
    session_token: str | None
    region: str


def session() -> boto3.Session:
    s = get_settings()
    return boto3.Session(profile_name=s.aws_profile or None, region_name=s.aws_region or None)


def resolve_credentials() -> AWSCredentials:
    """Resolve fresh credentials. Call per session so expiring SSO/role credentials stay valid."""
    s = get_settings()
    if s.aws_access_key_id and s.aws_secret_access_key:
        return AWSCredentials(
            s.aws_access_key_id, s.aws_secret_access_key, s.aws_session_token or None, s.aws_region
        )
    sess = session()
    creds = sess.get_credentials()
    if creds is None:
        raise AWSCredentialsMissing(
            "No AWS credentials found. Run `aws configure` (or `aws sso login`), set AWS_PROFILE, "
            "or set AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY in .env."
        )
    frozen = creds.get_frozen_credentials()
    return AWSCredentials(
        frozen.access_key, frozen.secret_key, frozen.token, s.aws_region or sess.region_name or "us-east-1"
    )
