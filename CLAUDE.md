# System Specification & Architecture Document (`CLAUDE.md`)

**Project:** Single-Tenant AI Voice Agent, Realtime CRM Pipeline & Calendar Booking Engine
**Architecture:** Monorepo — Python Voice Orchestrator (`FastAPI` + `Pipecat` + **Amazon Nova 2.5 Sonic**) + Realtime Web Dashboard (`Next.js App Router` + **local Supabase** + `shadcn/ui`)

> Revision 2 (2026-10-18). Changes from rev 1: Nova 2.5 Sonic speech-to-speech replaces Deepgram STT + text LLM + Cartesia/ElevenLabs TTS; Supabase runs locally via the Supabase CLI; Twilio is the only PSTN provider; post-call extraction uses Claude on Amazon Bedrock; the dashboard requires Supabase Auth.

---

## 1. System Overview & Architectural Guardrails

A self-hosted, single-tenant AI voice agent + CRM that handles **outbound speed-to-lead calls** and **inbound AI receptionist calls**, books appointments in real time via **Cal.com API v2**, and updates a realtime **Kanban CRM board** backed by **PostgreSQL (local Supabase)**.

### Strict Architectural Rules (DO NOT VIOLATE)
1. **Single-Tenant Only.** No multi-tenant org tables, OAuth marketplace flows, or billing wallets.
2. **Dual-Transport Voice Engine.**
   - `webrtc` mode: zero-cost browser mic/speaker testing via Pipecat `SmallWebRTCTransport` (SDP offer/answer over `POST /api/webrtc/offer`). The whole app works with **no phone number**.
   - `telephony` mode: **Twilio Media Streams only** — bidirectional 8 kHz μ-law over WebSocket (`/ws/voice`) via Pipecat `TwilioFrameSerializer`. Pipecat resamples to Nova Sonic's 16 kHz input / 24 kHz output.
3. **Unified Voice Loop.** Inbound, outbound, and WebRTC calls share one pipeline builder (`pipeline/voice_pipeline.py`), differing only in initial context and who speaks first.
4. **One realtime model.** Nova 2.5 Sonic (`amazon.nova-2-5-sonic`) is the only realtime speech model. Do NOT add separate STT, text-LLM, or TTS services to the call path.
5. **Strict Tool & Post-Call Contracts.** Mid-call bookings write the DB inside one atomic transaction. Post-call summaries and stage transitions use structured Pydantic outputs; stage routing is deterministic code, never LLM-decided.

---

## 2. Tech Stack

### Backend (`/apps/voice-engine`)
* Python `>=3.12`, managed with `uv`.
* `fastapi`, `uvicorn[standard]`
* `pipecat-ai[aws-nova-sonic,webrtc,websocket,silero] >=1.12.0`
* `asyncpg >=0.30` (raw SQL, no ORM), `pydantic >=2.10`, `pydantic-settings`
* `anthropic[bedrock]` — `AsyncAnthropicBedrockMantle` for post-call extraction (model `anthropic.claude-opus-5-5`, overridable)
* `apscheduler >=3.10.4` — retry/callback redials
* `httpx` — Cal.com v2; `twilio` — REST dialer, call updates, signature validation
* `pyjwt` — verifies Supabase access tokens on dashboard-called endpoints
* Tests: `pytest`, `pytest-asyncio`, `respx`

### Frontend (`/apps/web`)
* Next.js 15 (App Router, TypeScript strict), npm
* Tailwind CSS + `shadcn/ui` (Radix, Lucide)
* `@supabase/supabase-js` + `@supabase/ssr` (auth + Postgres Changes on `contacts`, `call_logs`, `appointments`)
* `@hello-pangea/dnd` (Kanban)
* `@pipecat-ai/client-js` + `@pipecat-ai/small-webrtc-transport` (browser simulator)

### Infra
* **Supabase CLI** (`supabase start`) runs Postgres (54322), API/Realtime (54321), Auth, and Studio (54323) locally in Docker.
* `docker-compose.yml` runs only `voice-engine` and `web`; it does not run the database.

---

## 3. Monorepo Directory Structure

```text
.
├── CLAUDE.md
├── .env.example
├── docker-compose.yml
├── supabase/
│   ├── config.toml
│   ├── seed.sql                       # admin user + sample contacts
│   └── migrations/
│       └── 0001_initial_schema.sql
├── apps/
│   ├── voice-engine/
│   │   ├── pyproject.toml
│   │   ├── Dockerfile
│   │   ├── src/
│   │   │   ├── main.py                # FastAPI app, lifespan (pool, scheduler), CORS
│   │   │   ├── config.py              # pydantic-settings
│   │   │   ├── db.py                  # asyncpg pool + all SQL
│   │   │   ├── schemas.py             # API payloads + PostCallExtraction
│   │   │   ├── routers/
│   │   │   │   ├── calls.py           # Twilio webhooks, /ws/voice, /api/webrtc/offer
│   │   │   │   └── leads.py           # POST /api/leads
│   │   │   ├── pipeline/
│   │   │   │   ├── voice_pipeline.py  # Nova Sonic pipeline builder (shared)
│   │   │   │   ├── prompts.py         # system-instruction compiler
│   │   │   │   ├── tools.py           # check_calendar_availability, book_calendar_appointment, transfer_to_human
│   │   │   │   └── post_call.py       # extraction + stage state machine
│   │   │   └── services/
│   │   │       ├── auth.py            # Supabase JWT / webhook-secret guard
│   │   │       ├── llm.py             # Bedrock Claude structured extraction
│   │   │       ├── telephony.py       # Twilio dialer, TwiML, bridge, signature check
│   │   │       ├── calendar.py        # Cal.com v2 client
│   │   │       └── retry_worker.py    # APScheduler job over next_follow_up_at
│   │   └── tests/
│   └── web/
│       ├── package.json
│       ├── middleware.ts              # session gate → /login
│       └── src/
│           ├── app/
│           │   ├── layout.tsx
│           │   ├── page.tsx           # KPI bar + Kanban + simulator
│           │   ├── login/page.tsx
│           │   └── settings/page.tsx  # Agent & Prompt Studio
│           ├── components/
│           │   ├── kanban-board.tsx
│           │   ├── contact-drawer.tsx
│           │   ├── browser-call-sim.tsx
│           │   └── ui/                # shadcn primitives
│           └── lib/
│               ├── supabase.ts
│               └── types.ts
```

---

## 4. Database Schema (`supabase/migrations/0001_initial_schema.sql`)

Same model as rev 1, with these changes:
* `agent_config`: `voice_provider` and `voice_id` are replaced by `nova_voice_id VARCHAR(40) NOT NULL DEFAULT 'tiffany'`. New columns: `timezone VARCHAR(64) NOT NULL DEFAULT 'America/Chicago'`, `business_hours_start SMALLINT NOT NULL DEFAULT 9`, `business_hours_end SMALLINT NOT NULL DEFAULT 19`.
* `call_logs.provider_call_id` stores the Twilio `CallSid` (or the WebRTC `pc_id`).
* `updated_at` triggers on `contacts` and `agent_config`.
* `REPLICA IDENTITY FULL` on `contacts`, `call_logs`, `appointments`; all three are in the `supabase_realtime` publication.
* RLS enabled on every table; one policy per table grants full CRUD to the `authenticated` role. The voice engine connects as `postgres` (bypasses RLS) and is the only writer for call flows.

The migration file is the source of truth for exact DDL.

---

## 5. Backend Functional Requirements

### 5.1 Endpoints
| Method & path | Auth | Behavior |
|---|---|---|
| `POST /api/leads` | Supabase JWT **or** `X-Webhook-Secret: $LEAD_WEBHOOK_SECRET` | Upsert contact by phone (stage `new_lead`). If `trigger_call_immediately`: set `calling_in_progress`, insert pending outbound `call_logs` row, `telephony.initiate_outbound_call(...)`. When `TELEPHONY_PROVIDER=webrtc_only`, the call row is created but no dial happens (use the simulator). |
| `POST /api/calls/inbound` | Twilio signature | Upsert contact by `From`, insert inbound `call_logs` row (`provider_call_id = CallSid`), return TwiML `<Connect><Stream url="wss://…/ws/voice">` with `<Parameter>` `contact_id`, `call_log_id`, `direction=inbound`. Target <100 ms. |
| `POST /api/calls/outbound-xml?contact_id&call_log_id` | Twilio signature | If `AnsweredBy` is a machine → `<Hangup/>` and post-call routes it as `voicemail`. Else TwiML stream with `direction=outbound`. |
| `POST /api/calls/status` | Twilio signature | Status + recording callbacks → `duration_seconds`, `recording_url`; `no-answer`/`busy`/`failed` → voicemail routing. |
| `WS /ws/voice` | Twilio stream params | Twilio Media Streams → shared pipeline. |
| `POST /api/webrtc/offer`, `PATCH /api/webrtc/offer` | Supabase JWT | SDP offer/answer + ICE trickle for the browser simulator. `requestData`: `{direction: "inbound"|"outbound", contact_id?}`. |
| `GET /health` | none | Liveness. |

### 5.2 Pipeline (`pipeline/voice_pipeline.py`)
```text
transport.input() → user_aggregator → AWSNovaSonicLLMService(tools, voice, system_instruction)
  → transport.output() → assistant_aggregator
```
* `LLMContextAggregatorPair` (realtime mode auto-detected). Transcript lines are captured from `on_user_turn_message_added` / `on_assistant_turn_stopped` with timestamps.
* Turn-taking and barge-in are native to Nova Sonic. Its ~8 min session limit is handled by Pipecat's built-in session continuation.
* **First turn.** Nova 2.5 Sonic speaks when the context ends in a user message and an `LLMRunFrame` is queued.
  * Inbound / WebRTC-inbound: on client connect, queue `LLMRunFrame` with a seed user message telling the agent to deliver the compiled `inbound_greeting`. Any upcoming confirmed appointment is noted in the system instruction ("Caller already has a confirmed booking on {scheduled_at}…").
  * Outbound: the agent waits for the callee to speak ("Hello?"). If no user turn starts within **1200 ms** of connect, queue the greeting run anyway.
* RTVI is enabled so the browser simulator receives live transcripts and function-call events.

### 5.3 In-call tools (`pipeline/tools.py`)
The system instruction requires a short filler phrase ("Let me check the calendar real quick…") **before** each tool call. Nova Sonic produces all speech, so there is no `tts.say()`.
1. `check_calendar_availability(date_str, timezone="America/Chicago")`: `GET https://api.cal.com/v2/slots?eventTypeId=…&start=…&end=…&timeZone=…` with `cal-api-version: 2024-09-04`. Returns the top 3 human-readable slots plus their ISO values.
2. `book_calendar_appointment(slot_iso, attendee_name, attendee_email, notes)`: `POST https://api.cal.com/v2/bookings` with `cal-api-version: 2024-08-13`. On 201, run one DB transaction: insert `appointments` and set `contacts.stage='appointment_booked'`, name, and email.
3. `transfer_to_human(reason)`: reads `human_transfer_phone`. The model says "Hold on just a second while I connect you with a specialist." Then the call is updated via Twilio REST with TwiML `<Dial>` and `stage='transferred_to_human'` is set. In WebRTC mode it only updates the stage and tells the caller the transfer was simulated.

### 5.4 Post-call (`pipeline/post_call.py`)
On transport disconnect:
1. Transcript → `services/llm.extract()` → `PostCallExtraction` (Claude on Bedrock, `messages.parse(output_format=PostCallExtraction)`). An empty transcript → `voicemail` without calling the LLM.
2. Update `call_logs` with transcript, duration, outcome, sentiment, summary, and extracted_data. Merge `extracted_custom_fields` into `contacts.custom_fields`, and set `last_contacted_at`.
3. Deterministic routing, skipped when the stage is already `appointment_booked` or `transferred_to_human`:
   * `voicemail`: if `retry_count < 3`, set `no_answer_retry`, increment `retry_count`, and set `next_follow_up_at = now + 4h`. Otherwise set `not_interested`.
   * `follow_up_requested`: set `follow_up_requested` and `next_follow_up_at = requested_callback_iso ?? now + 24h`.
   * `not_interested`: set `not_interested` and `next_follow_up_at = NULL`.
   * `booked` / `transferred`: no change (the tools already set the stage).

### 5.5 Retry worker (`services/retry_worker.py`)
APScheduler interval job (60 s). Selects contacts in `no_answer_retry`/`follow_up_requested` with `next_follow_up_at <= now()`, but only when the local time in `agent_config.timezone` falls within business hours. It claims each row atomically (`FOR UPDATE SKIP LOCKED` → `calling_in_progress`, `next_follow_up_at = NULL`) and dials. It does nothing when `TELEPHONY_PROVIDER=webrtc_only`.

---

## 6. Frontend UI/UX

Clean, high-contrast shadcn/ui: 56px top nav, 8px grid, zinc palette plus one accent (`emerald-600` for active calls and booked), weights 400/600.

* **Login (`/login`)**: email + password via Supabase Auth. `middleware.ts` redirects unauthenticated users here.
* **Main (`/`)**
  * KPI bar: Total Leads, Active Calls Now (pulsing), Appointments Booked, Conversion %. Actions: "+ New Lead / Dial" and "Test Agent in Browser ($0 WebRTC)".
  * 7-column Kanban mapped 1:1 to `pipeline_stage`. Realtime via `supabase.channel('crm-pipeline')`. Drag-and-drop updates `stage`. Cards show name or formatted phone, deal value, latest sentiment badge, tags, and relative `last_contacted_at`. Stage changes animate.
  * Contact drawer (Sheet, 480px):
    * `Activity & Calls` tab: audio player, summary, outcome badge, collapsible timestamped transcript.
    * `Contact Details` tab: editable name, email, phone, stage, and a custom_fields key-value editor.
  * Browser call simulator: Inbound/Outbound toggle, connect/disconnect, live transcript, tool-call log (RTVI events).
* **Settings (`/settings`)**: form bound to `agent_config` (id=1). Fields: agent name, company, Nova Sonic voice selector, both greeting templates, qualification-question list builder, objection-handler key-value list, Cal.com event type ID, transfer phone, timezone, and business hours.

---

## 7. Environment Variables
See `.env.example`, which is the contract. Keys: Supabase URLs and keys, `DATABASE_URL`, AWS credentials from the AWS CLI chain (`AWS_PROFILE` or the default `~/.aws` profile; explicit `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` optional override) + `AWS_REGION=us-east-1`, `NOVA_SONIC_MODEL_ID`, `BEDROCK_CLAUDE_MODEL_ID`, Twilio creds, `TELEPHONY_PROVIDER` (`twilio` | `webrtc_only`), `PUBLIC_VOICE_WEBHOOK_BASE_URL`, `CAL_API_KEY`, and `LEAD_WEBHOOK_SECRET`.

---

## 8. Phased Implementation (verify each gate before proceeding)
1. **DB & FastAPI skeleton.** Migration, seed, `db.py`, `leads.py`. Gate: `supabase db reset` is clean; pytest covers lead upsert and the pending call row.
2. **Nova Sonic pipeline & Cal.com tools (WebRTC first).** Gate: unit tests for prompts, stage routing, and booking transaction/rollback (Cal.com mocked); a manual browser call books a slot and moves the card to `appointment_booked`.
3. **Twilio adapters & retry worker.** Gate: TwiML/route tests, inbound latency check, retry-selection test; live PSTN via ngrok when creds exist.
4. **Next.js dashboard.** Gate: `npm run build` + typecheck; sign in, then see realtime card moves and a simulator call end-to-end.

## 9. Dev commands
```bash
supabase start && supabase db reset          # DB + seed
cd apps/voice-engine && uv run uvicorn src.main:app --reload --port 8000
cd apps/voice-engine && uv run pytest
cd apps/web && npm run dev                    # http://localhost:3000
```
