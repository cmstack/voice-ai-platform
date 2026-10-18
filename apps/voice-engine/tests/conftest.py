"""Tests run against the local Supabase Postgres (`supabase start`)."""

import time

import httpx
import jwt
import pytest
from fastapi import FastAPI

from src import db
from src.config import get_settings

TEST_PHONE_PREFIX = "+1555010"


@pytest.fixture(scope="session", autouse=True)
async def pool():
    await db.init_pool()
    yield db.pool()
    await db.pool().execute("DELETE FROM contacts WHERE phone LIKE $1", TEST_PHONE_PREFIX + "%")
    await db.close_pool()


@pytest.fixture(autouse=True)
async def clean_test_contacts(pool):
    await pool.execute("DELETE FROM contacts WHERE phone LIKE $1", TEST_PHONE_PREFIX + "%")
    yield


@pytest.fixture
def auth_headers() -> dict[str, str]:
    token = jwt.encode(
        {"sub": "11111111-1111-1111-1111-111111111111", "role": "authenticated",
         "aud": "authenticated", "exp": int(time.time()) + 600},
        get_settings().supabase_jwt_secret,
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


def make_client(*routers) -> httpx.AsyncClient:
    app = FastAPI()
    for r in routers:
        app.include_router(r)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
