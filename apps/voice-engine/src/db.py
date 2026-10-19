"""asyncpg pool and every SQL statement the voice engine runs."""

import json
from datetime import datetime
from typing import Any
from uuid import UUID

import asyncpg

from src.config import get_settings

_pool: asyncpg.Pool | None = None


async def _init_connection(conn: asyncpg.Connection) -> None:
    for typ in ("json", "jsonb"):
        await conn.set_type_codec(typ, encoder=json.dumps, decoder=json.loads, schema="pg_catalog")


async def init_pool(dsn: str | None = None) -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            dsn or get_settings().dsn, min_size=1, max_size=10, init=_init_connection
        )
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("Database pool not initialised")
    return _pool


def _row(record: asyncpg.Record | None) -> dict[str, Any] | None:
    return dict(record) if record is not None else None


# --- auth -------------------------------------------------------------------


async def user_role(user_id: UUID) -> str | None:
    """The user's CURRENT app_metadata.role from auth.users (None if the user is gone)."""
    return await pool().fetchval(
        "SELECT raw_app_meta_data ->> 'role' FROM auth.users WHERE id = $1 AND deleted_at IS NULL", user_id
    )


# --- agent_config -----------------------------------------------------------


async def get_agent_config() -> dict[str, Any]:
    row = await pool().fetchrow("SELECT * FROM agent_config WHERE id = 1")
    return dict(row)


# --- contacts ---------------------------------------------------------------


async def get_contact(contact_id: UUID) -> dict[str, Any] | None:
    return _row(await pool().fetchrow("SELECT * FROM contacts WHERE id = $1", contact_id))


async def upsert_lead(
    phone: str,
    full_name: str | None,
    email: str | None,
    custom_fields: dict[str, Any],
    consent_source: str | None = None,
) -> dict[str, Any]:
    """Speed-to-lead upsert: a fresh form submission always resets the card to new_lead.

    Consent is only ever added, never cleared: a later submission without consent keeps the record.
    """
    row = await pool().fetchrow(
        """
        INSERT INTO contacts (phone, full_name, email, custom_fields, stage, consent_at, consent_source)
        VALUES ($1, $2, $3, $4, 'new_lead', CASE WHEN $5::text IS NULL THEN NULL ELSE NOW() END, $5)
        ON CONFLICT (phone) DO UPDATE SET
            full_name = COALESCE(EXCLUDED.full_name, contacts.full_name),
            email = COALESCE(EXCLUDED.email, contacts.email),
            custom_fields = COALESCE(contacts.custom_fields, '{}'::jsonb) || EXCLUDED.custom_fields,
            stage = 'new_lead',
            retry_count = 0,
            next_follow_up_at = NULL,
            consent_at = COALESCE(EXCLUDED.consent_at, contacts.consent_at),
            consent_source = COALESCE(EXCLUDED.consent_source, contacts.consent_source)
        RETURNING *
        """,
        phone,
        full_name,
        email,
        custom_fields,
        consent_source,
    )
    return dict(row)


async def record_consent(contact_id: UUID, source: str) -> None:
    """Record consent if none is on file yet (keeps the original, earliest record)."""
    await pool().execute(
        "UPDATE contacts SET consent_at = NOW(), consent_source = $2 WHERE id = $1 AND consent_at IS NULL",
        contact_id,
        source,
    )


async def queue_dial(contact_id: UUID, at: datetime) -> None:
    """Keep the card where it is but have the retry worker dial it at `at`."""
    await pool().execute(
        "UPDATE contacts SET next_follow_up_at = $2 WHERE id = $1", contact_id, at
    )


async def count_dials(*, since: datetime, contact_id: UUID | None = None) -> int:
    """Real dial attempts (rows that got a provider call id) since `since`."""
    return await pool().fetchval(
        """
        SELECT count(*) FROM call_logs
        WHERE direction = 'outbound' AND provider_call_id IS NOT NULL AND created_at >= $1
          AND ($2::uuid IS NULL OR contact_id = $2)
        """,
        since,
        contact_id,
    )


async def delete_call_log(call_log_id: UUID) -> None:
    await pool().execute("DELETE FROM call_logs WHERE id = $1 AND outcome IS NULL", call_log_id)


async def count_bookings_since(since: datetime) -> int:
    return await pool().fetchval("SELECT count(*) FROM appointments WHERE created_at >= $1", since)


async def count_upcoming_appointments(contact_id: UUID) -> int:
    return await pool().fetchval(
        "SELECT count(*) FROM appointments WHERE contact_id = $1 AND status = 'confirmed' AND scheduled_at >= NOW()",
        contact_id,
    )


async def set_stage(contact_id: UUID, stage: str) -> None:
    await pool().execute(
        "UPDATE contacts SET stage = $2::pipeline_stage WHERE id = $1", contact_id, stage
    )


async def start_outbound_call(contact_id: UUID, direction: str = "outbound") -> UUID:
    """Mark the contact as being called and create the pending call_logs row atomically."""
    async with pool().acquire() as conn, conn.transaction():
        await conn.execute(
            """
            UPDATE contacts
            SET stage = 'calling_in_progress', last_contacted_at = NOW(), next_follow_up_at = NULL
            WHERE id = $1
            """,
            contact_id,
        )
        return await conn.fetchval(
            "INSERT INTO call_logs (contact_id, direction) VALUES ($1, $2) RETURNING id",
            contact_id,
            direction,
        )


async def reschedule(contact_id: UUID, stage: str, delay_minutes: int) -> None:
    await pool().execute(
        """
        UPDATE contacts
        SET stage = $2::pipeline_stage, next_follow_up_at = NOW() + make_interval(mins => $3)
        WHERE id = $1
        """,
        contact_id,
        stage,
        delay_minutes,
    )


async def set_provider_call_id(call_log_id: UUID, provider_call_id: str) -> None:
    await pool().execute(
        "UPDATE call_logs SET provider_call_id = $2 WHERE id = $1", call_log_id, provider_call_id
    )


async def inbound_call_lookup(
    phone: str, provider_call_id: str | None, direction: str = "inbound"
) -> tuple[dict[str, Any], UUID, list[dict[str, Any]]]:
    """Caller-ID upsert + call row + upcoming appointments in one round trip transaction."""
    async with pool().acquire() as conn, conn.transaction():
        contact = await conn.fetchrow(
            """
            INSERT INTO contacts (phone, last_contacted_at) VALUES ($1, NOW())
            ON CONFLICT (phone) DO UPDATE SET last_contacted_at = NOW()
            RETURNING *
            """,
            phone,
        )
        call_log_id = await conn.fetchval(
            """
            INSERT INTO call_logs (contact_id, direction, provider_call_id) VALUES ($1, $2, $3)
            ON CONFLICT (provider_call_id) DO UPDATE SET direction = EXCLUDED.direction
            RETURNING id
            """,
            contact["id"],
            direction,
            provider_call_id,
        )
        appts = await conn.fetch(
            """
            SELECT id, scheduled_at, notes FROM appointments
            WHERE contact_id = $1 AND status = 'confirmed' AND scheduled_at >= NOW()
            ORDER BY scheduled_at
            """,
            contact["id"],
        )
    return dict(contact), call_log_id, [dict(a) for a in appts]


async def upcoming_appointments(contact_id: UUID) -> list[dict[str, Any]]:
    rows = await pool().fetch(
        """
        SELECT id, scheduled_at, notes FROM appointments
        WHERE contact_id = $1 AND status = 'confirmed' AND scheduled_at >= NOW()
        ORDER BY scheduled_at
        """,
        contact_id,
    )
    return [dict(r) for r in rows]


# --- booking ----------------------------------------------------------------


async def record_booking(
    *,
    contact_id: UUID,
    call_log_id: UUID | None,
    cal_booking_uid: str,
    scheduled_at: datetime,
    notes: str | None,
    attendee_name: str,
    attendee_email: str,
) -> UUID:
    async with pool().acquire() as conn, conn.transaction():
        appointment_id = await conn.fetchval(
            """
            INSERT INTO appointments (contact_id, call_log_id, cal_booking_uid, scheduled_at, notes)
            VALUES ($1, $2, $3, $4, $5)
            RETURNING id
            """,
            contact_id,
            call_log_id,
            cal_booking_uid,
            scheduled_at,
            notes,
        )
        await conn.execute(
            """
            UPDATE contacts
            SET stage = 'appointment_booked', full_name = $2, email = $3, next_follow_up_at = NULL
            WHERE id = $1
            """,
            contact_id,
            attendee_name,
            attendee_email,
        )
    return appointment_id


# --- post-call --------------------------------------------------------------


async def finalize_call(
    *,
    call_log_id: UUID,
    contact_id: UUID,
    transcript: list[dict[str, Any]],
    duration_seconds: int | None,
    outcome: str,
    sentiment: str,
    summary: str,
    extracted_data: dict[str, Any],
    custom_fields: dict[str, Any],
    stage_update_fn,
) -> dict[str, Any]:
    """Persist the call analysis and apply the stage transition under a row lock.

    `stage_update_fn(contact_row) -> dict | None` returns the columns to set (or None).
    """
    async with pool().acquire() as conn, conn.transaction():
        await conn.execute(
            """
            UPDATE call_logs SET
                transcript = $2,
                duration_seconds = GREATEST(COALESCE(duration_seconds, 0), COALESCE($3, 0)),
                outcome = $4, sentiment = $5, summary = $6, extracted_data = $7
            WHERE id = $1
            """,
            call_log_id,
            transcript,
            duration_seconds,
            outcome,
            sentiment,
            summary,
            extracted_data,
        )
        contact = dict(
            await conn.fetchrow("SELECT * FROM contacts WHERE id = $1 FOR UPDATE", contact_id)
        )
        update = stage_update_fn(contact) or {}
        await conn.execute(
            """
            UPDATE contacts SET
                custom_fields = COALESCE(custom_fields, '{}'::jsonb) || $2,
                last_contacted_at = NOW(),
                stage = COALESCE($3::pipeline_stage, stage),
                retry_count = COALESCE($4, retry_count),
                next_follow_up_at = CASE WHEN $5 THEN $6 ELSE next_follow_up_at END
            WHERE id = $1
            """,
            contact_id,
            custom_fields,
            update.get("stage"),
            update.get("retry_count"),
            "next_follow_up_at" in update,
            update.get("next_follow_up_at"),
        )
        return dict(await conn.fetchrow("SELECT * FROM contacts WHERE id = $1", contact_id))


async def update_call_status(
    provider_call_id: str, duration_seconds: int | None, recording_url: str | None
) -> dict[str, Any] | None:
    return _row(
        await pool().fetchrow(
            """
            UPDATE call_logs SET
                duration_seconds = COALESCE($2, duration_seconds),
                recording_url = COALESCE($3, recording_url)
            WHERE provider_call_id = $1
            RETURNING *
            """,
            provider_call_id,
            duration_seconds,
            recording_url,
        )
    )


async def get_call_log(call_log_id: UUID) -> dict[str, Any] | None:
    return _row(await pool().fetchrow("SELECT * FROM call_logs WHERE id = $1", call_log_id))


# --- retry worker -----------------------------------------------------------


async def claim_due_follow_ups(limit: int = 5) -> list[tuple[dict[str, Any], UUID]]:
    """Atomically claim due callbacks so two workers never dial the same contact."""
    claimed: list[tuple[dict[str, Any], UUID]] = []
    async with pool().acquire() as conn, conn.transaction():
        rows = await conn.fetch(
            """
            SELECT * FROM contacts
            WHERE stage IN ('new_lead', 'no_answer_retry', 'follow_up_requested')
              AND next_follow_up_at IS NOT NULL AND next_follow_up_at <= NOW()
            ORDER BY next_follow_up_at
            LIMIT $1
            FOR UPDATE SKIP LOCKED
            """,
            limit,
        )
        for row in rows:
            await conn.execute(
                """
                UPDATE contacts
                SET stage = 'calling_in_progress', next_follow_up_at = NULL, last_contacted_at = NOW()
                WHERE id = $1
                """,
                row["id"],
            )
            call_log_id = await conn.fetchval(
                "INSERT INTO call_logs (contact_id, direction) VALUES ($1, 'outbound') RETURNING id",
                row["id"],
            )
            claimed.append((dict(row), call_log_id))
    return claimed


# --- retention ----------------------------------------------------------------


async def purge_call_content(cutoff: datetime) -> list[tuple[UUID, str | None]]:
    """Clear transcript, recording and extracted details of calls created before `cutoff`."""
    rows = await pool().fetch(
        """
        WITH old AS (
            SELECT id, recording_url FROM call_logs
            WHERE created_at < $1
              AND (transcript <> '[]'::jsonb OR recording_url IS NOT NULL OR extracted_data <> '{}'::jsonb)
            FOR UPDATE
        )
        UPDATE call_logs c
        SET transcript = '[]'::jsonb, recording_url = NULL, extracted_data = '{}'::jsonb
        FROM old WHERE c.id = old.id
        RETURNING c.id, old.recording_url
        """,
        cutoff,
    )
    return [(r["id"], r["recording_url"]) for r in rows]
