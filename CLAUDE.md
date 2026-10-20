# System Specification & Architecture Document (`CLAUDE.md`)

**Project:** Single-Tenant AI Voice Agent, Realtime CRM Pipeline & Calendar Booking Engine
**Architecture:** Monorepo — Python Voice Orchestrator (`FastAPI` + `Pipecat` + **Amazon Nova 2.5 Sonic**) + Realtime Web Dashboard (`Next.js App Router` + **local Supabase** + `shadcn/ui`)

> Revision 5 (2026-10-19).
> * **Rev 2 (changes from rev 1):** Nova 2.5 Sonic speech-to-speech replaces Deepgram STT + text LLM + Cartesia/ElevenLabs TTS; Supabase runs locally via the Supabase CLI; Twilio is the only PSTN provider; post-call extraction uses Claude on Amazon Bedrock; the dashboard requires Supabase Auth.
> * **Rev 3:** AWS credentials come from the AWS CLI credential chain; a Flows-style conversation state machine runs inside the tools (§5.3); post-call custom fields are extracted as key/value pairs; the dashboard has design tokens, dark mode and full loading, empty and error states (§6).
> * **Rev 4:** security hardening (§5.6): admin-only access (sign-up off, `is_admin()` RLS, admin claim on the API), fail-closed production config, an outbound dial guard (country allowlist, consent, calling hours, dial limits), prompt-injection hardening, in-call tool caps, a Twilio WebSocket signature check, API rate limits, and opt-in call recording with a spoken disclosure.
> * **Rev 5:** second security review (§5.6):
>   * per-call signed stream URLs, and one stream per call;
>   * a global concurrent-call cap, an hourly booking cap, and country checks on transfers;
>   * auto-detected production mode, INFO logging, and API docs hidden in production;
>   * an authenticated recording proxy, and a daily retention purge;
>   * least-privilege grants, and immediate admin revocation (RLS checks `auth.users`);
>   * dashboard security headers (CSP, no framing), and Realtime authenticated before subscribing.

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
5. **Every real dial goes through `services/dial_guard.py`.** Never call Twilio's `calls.create` anywhere else.
6. **Caller-supplied text is untrusted.** Clean it with `pipeline/sanitize.py` before storing it or placing it in a prompt, where it goes inside the `<caller_data>` block.
7. **Strict Tool & Post-Call Contracts.** Mid-call bookings write the DB inside one atomic transaction. Post-call summaries and stage transitions use structured Pydantic outputs; stage routing is deterministic code, never LLM-decided.

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
* `pyjwt[crypto]` — verifies Supabase access tokens (HS256 secret or the local JWKS) on dashboard-called endpoints
* `boto3` — resolves AWS credentials through the standard chain (`services/aws.py`)
* Tests: `pytest`, `pytest-asyncio`, `respx`

### Frontend (`/apps/web`)
* Next.js 15 (App Router, TypeScript strict), npm
* Tailwind CSS + `shadcn/ui` (Radix, Lucide)
* `@supabase/supabase-js` + `@supabase/ssr` (auth + Postgres Changes on `contacts`, `call_logs`, `appointments`)
* `@hello-pangea/dnd` (Kanban)
* `@pipecat-ai/client-js` + `@pipecat-ai/small-webrtc-transport` (browser simulator)
* `next-themes` (system/light/dark), Geist + Geist Mono via `next/font`

### Infra
* **Supabase CLI** (`supabase start`) runs Postgres (54322), API/Realtime (54321), Auth, and Studio (54323) locally in Docker.
* `docker-compose.yml` runs only `voice-engine` and `web`; it does not run the database.

---

## 3. Monorepo Directory Structure

```text
.
├── CLAUDE.md
├── README.md                          # how to run it
├── .env.example
├── docker-compose.yml
├── supabase/
│   ├── config.toml
│   ├── seed.sql                       # admin user + sample contacts
│   └── migrations/
│       ├── 0001_initial_schema.sql
│       ├── 0002_security.sql          # is_admin() RLS, consent columns, opt-in recording
│       └── 0003_least_privilege.sql   # revoke anon/TRUNCATE grants; is_admin() checks auth.users
├── apps/
│   ├── voice-engine/
│   │   ├── pyproject.toml
│   │   ├── Dockerfile
│   │   ├── src/
│   │   │   ├── main.py                # FastAPI app, lifespan (pool, scheduler), CORS, docs toggle
│   │   │   ├── logging_config.py      # loguru level (INFO default; DEBUG leaks prompts)
│   │   │   ├── config.py              # pydantic-settings
│   │   │   ├── db.py                  # asyncpg pool + all SQL
│   │   │   ├── schemas.py             # API payloads, PostCallExtraction (+ model-facing Output variant)
│   │   │   ├── routers/
│   │   │   │   ├── calls.py           # Twilio webhooks, /ws/voice, /api/webrtc/offer
│   │   │   │   ├── leads.py           # POST /api/leads
│   │   │   │   └── recordings.py      # GET /api/recordings/{id} (admin-only Twilio proxy)
│   │   │   ├── pipeline/
│   │   │   │   ├── voice_pipeline.py  # Nova Sonic pipeline builder (shared)
│   │   │   │   ├── prompts.py         # system-instruction compiler (stage overview, dates, UTC offset)
│   │   │   │   ├── flow.py            # conversation state machine: stages, tool guards, next-step text
│   │   │   │   ├── sanitize.py        # cleans caller-supplied text before storage / prompts
│   │   │   │   ├── tools.py           # the 6 in-call tools, each wrapped by the flow guard
│   │   │   │   └── post_call.py       # extraction, flow ground truth, pipeline-stage routing
│   │   │   └── services/
│   │   │       ├── auth.py            # Supabase JWT / webhook-secret guard
│   │   │       ├── aws.py             # AWS credential chain (CLI profile, SSO, role, or .env keys)
│   │   │       ├── dial_guard.py      # outbound dialing policy (country, consent, hours, limits)
│   │   │       ├── rate_limit.py      # per-client API limits + concurrent-call caps
│   │   │       ├── retention.py       # daily purge of old transcripts/recordings
│   │   │       ├── llm.py             # Bedrock Claude structured extraction
│   │   │       ├── telephony.py       # Twilio dialer, TwiML, bridge, signature check
│   │   │       ├── calendar.py        # Cal.com v2 client
│   │   │       └── retry_worker.py    # APScheduler job over next_follow_up_at
│   │   └── tests/                     # incl. test_flow.py, test_security.py, test_security_review2.py
│   └── web/
│       ├── package.json
│       └── src/
│           ├── middleware.ts          # session gate → /login
│           ├── app/
│           │   ├── globals.css        # design tokens (light + dark)
│           │   ├── layout.tsx         # fonts, theme provider, toaster
│           │   ├── page.tsx           # KPI strip + Kanban
│           │   ├── login/page.tsx
│           │   └── settings/page.tsx  # Agent & Prompt Studio
│           ├── components/
│           │   ├── top-nav.tsx
│           │   ├── kanban-board.tsx   # board, skeleton, live indicator
│           │   ├── contact-drawer.tsx # calls, transcript, step path, details form
│           │   ├── browser-call-sim.tsx
│           │   ├── new-lead-dialog.tsx
│           │   ├── theme-provider.tsx / theme-toggle.tsx
│           │   └── ui/                # shadcn primitives (Radix), tokenized
│           ├── hooks/use-crm.ts       # contacts + latest calls via Postgres Changes
│           └── lib/
│               ├── supabase.ts        # browser client + voice-engine fetch
│               ├── format.ts
│               └── types.ts
```

---

## 4. Database Schema (`supabase/migrations/0001_initial_schema.sql`)

Same model as rev 1, with these changes:
* `agent_config`: `voice_provider` and `voice_id` are replaced by `nova_voice_id VARCHAR(40) NOT NULL DEFAULT 'tiffany'`. New columns: `timezone VARCHAR(64) NOT NULL DEFAULT 'America/Chicago'`, `business_hours_start SMALLINT NOT NULL DEFAULT 9`, `business_hours_end SMALLINT NOT NULL DEFAULT 19`.
* `call_logs.provider_call_id` stores the Twilio `CallSid` (or the WebRTC `pc_id`).
* `updated_at` triggers on `contacts` and `agent_config`.
* `REPLICA IDENTITY FULL` on `contacts`, `call_logs`, `appointments`; all three are in the `supabase_realtime` publication.
* RLS is enabled on every table. Since `0002_security.sql`, one `admin_all` policy per table allows access only when `public.is_admin()` is true, meaning the JWT's `app_metadata.role` is `'admin'`. A signed-in non-admin sees nothing, including through Realtime. The voice engine connects as `postgres` (bypasses RLS) and is the only writer for call flows.
* `contacts.consent_at` / `consent_source` record prior consent to be called. Consent is added, never cleared.
* `agent_config.record_calls` (default `false`) and `recording_disclosure` control opt-in recording.
* Since `0003_least_privilege.sql`:
  * `anon` has no table privileges, and `authenticated` has only SELECT/INSERT/UPDATE/DELETE (no TRUNCATE, which RLS cannot filter).
  * `is_admin()` is `SECURITY DEFINER` and also requires the user's *current* `auth.users.raw_app_meta_data.role = 'admin'`, so revoking an admin applies to the next query.
  * Policies call it as `(SELECT public.is_admin())` so it runs once per statement.

The migration file is the source of truth for exact DDL.

---

## 5. Backend Functional Requirements

### 5.1 Endpoints
| Method & path | Auth | Behavior |
|---|---|---|
| `POST /api/leads` | Admin JWT **or** `X-Webhook-Secret: $LEAD_WEBHOOK_SECRET`; rate-limited | Upsert contact by phone (stage `new_lead`). `consent: true` (+ `consent_source`) records call consent. If `trigger_call_immediately`, the dial guard runs first. When blocked, the response carries `blocked_reason` and, for temporary blocks, `next_attempt_at`: the dial is queued and no call row is created. Otherwise: set `calling_in_progress`, insert a pending outbound `call_logs` row and dial. When `TELEPHONY_PROVIDER=webrtc_only`, the call row is created but no dial happens (use the simulator). |
| `POST /api/calls/inbound` | Twilio signature | When all `MAX_CONCURRENT_CALLS` slots are taken, returns a spoken "lines are busy" + `<Hangup/>`. A withheld caller ID gets its own `anon:<CallSid tail>` contact, which can never be dialed. Otherwise: upsert contact by `From`, insert inbound `call_logs` row (`provider_call_id = CallSid`), return TwiML `<Connect><Stream url="wss://…/ws/voice?call_log_id=…">` (a per-call URL) with `<Parameter>` `contact_id`, `call_log_id`, `direction=inbound`. Target <100 ms. |
| `POST /api/calls/outbound-xml?contact_id&call_log_id` | Twilio signature | If `AnsweredBy` is a machine → `<Hangup/>` and post-call routes it as `voicemail`. Else TwiML stream with `direction=outbound`. |
| `POST /api/calls/status` | Twilio signature | Status + recording callbacks → `duration_seconds`, `recording_url`; `no-answer`/`busy`/`failed` → voicemail routing. |
| `WS /ws/voice?call_log_id=…` | `X-Twilio-Signature` over the per-call URL, plus a live call row | The URL's call id must match the stream's `call_log_id`, the call must still be open and belong to that `CallSid`, and only one stream per call is accepted. It holds a `MAX_CONCURRENT_CALLS` slot. Twilio Media Streams → shared pipeline. |
| `GET /api/recordings/{call_log_id}` | Admin JWT | Fetches the call's recording from `api.twilio.com` with the account credentials (no other host is fetched) and returns it with `Cache-Control: private, no-store`. |
| `POST /api/webrtc/offer`, `PATCH /api/webrtc/offer` | Admin JWT; rate-limited; at most `MAX_CONCURRENT_BROWSER_CALLS` at once, and counted in `MAX_CONCURRENT_CALLS` | SDP offer/answer + ICE trickle for the browser simulator. `requestData`: `{direction: "inbound"|"outbound", contact_id?}`. |
| `GET /health` | none | Liveness. |
| `GET /docs`, `/redoc`, `/openapi.json` | none | Only when `api_docs_enabled` (development by default). |

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
* **Agent-initiated hangup.** `end_call` queues an `EndFrame` after a short delay so the goodbye plays. On Twilio, the serializer's auto hang-up ends the phone call; on WebRTC, the session closes. Post-call runs either way.

### 5.3 Conversation flow & in-call tools (`pipeline/flow.py`, `pipeline/tools.py`)
Nova Sonic fixes its prompt and tools when the session opens, so Pipecat Flows' per-node prompt swaps do not apply. Instead, a Flows-style state machine runs inside the tools:
* **Stages:** `qualify → schedule → booked`, plus `callback`, `transferred` and `closed` exits. Each stage allows a fixed set of tools. An out-of-order call is rejected and told what to do now.
* **Every tool result carries `stage` and `next`.** `next` holds the current node's instructions, which Nova reads mid-call. The system instruction includes the stage overview and tells the model to follow `next`.
* **Tools (all registered once):**
  * `save_qualification`: one string field per configured question (keys from `answer_key()`) plus `ready_to_book`.
  * `check_calendar_availability`: Cal.com `GET /v2/slots`, `cal-api-version: 2024-09-04`, top 3 spread slots.
  * `book_calendar_appointment`: only slots offered on this call, valid email required. Cal.com `POST /v2/bookings`, `cal-api-version: 2024-08-13`. On 201, one DB transaction: insert `appointments`, set the contact to `appointment_booked`.
  * `schedule_callback`: a future ISO time with an offset.
  * `transfer_to_human`: Twilio `<Dial>`; simulated in WebRTC calls.
  * `end_call`: reason `completed|not_interested|wrong_number|voicemail`. Hangs up after the goodbye.
* **Caps:** at most `MAX_AVAILABILITY_CHECKS_PER_CALL` calendar lookups and `MAX_CALLBACKS_PER_CALL` callbacks per call. Callbacks are limited to 60 days ahead. At most `MAX_UPCOMING_APPOINTMENTS_PER_CONTACT` upcoming bookings per contact, and `MAX_BOOKINGS_PER_HOUR` bookings across all callers. `transfer_to_human` refuses transfer numbers outside `ALLOWED_DIAL_COUNTRY_CODES`, and `bridge_call` re-checks. Caller text (answers, names, notes, reasons) is cleaned and length-capped.
* `schedule_callback` records the caller's request as call consent (`callback requested by caller on call …`).
* The model says a short filler only before the calendar tools.
* The prompt lists the next 7 dates and the UTC offset so weekdays and callback times resolve correctly.

### 5.4 Post-call (`pipeline/post_call.py`)
On transport disconnect:
1. Transcript → `services/llm.extract()` → `PostCallExtraction` (Claude on Bedrock via `messages.parse`). An empty transcript → `voicemail` without calling the LLM.
   The model-facing schema (`PostCallExtractionOutput`) returns custom fields as `[{key, value}]` pairs and converts them to a dict, because structured outputs steer an open-ended object to `{}`.
   What the in-call flow recorded is ground truth and overrides the classifier:
   * booked or transferred keeps that outcome;
   * a scheduled callback → `follow_up_requested` at that exact time;
   * `end_call` not_interested or wrong_number → `not_interested`;
   * saved qualification answers merge into `custom_fields`;
   * the stage path is stored at `call_logs.extracted_data.flow`.
2. Update `call_logs` with transcript, duration, outcome, sentiment, summary, and extracted_data. Merge `extracted_custom_fields` into `contacts.custom_fields`, and set `last_contacted_at`.
3. Deterministic routing, skipped when the stage is already `appointment_booked` or `transferred_to_human`:
   * `voicemail`: if `retry_count < 3`, set `no_answer_retry`, increment `retry_count`, and set `next_follow_up_at = now + 4h`. Otherwise set `not_interested`.
   * `follow_up_requested`: set `follow_up_requested` and `next_follow_up_at = requested_callback_iso ?? now + 24h`.
   * `not_interested`: set `not_interested` and `next_follow_up_at = NULL`.
   * `booked` / `transferred`: no change (the tools already set the stage).

### 5.5 Retry worker (`services/retry_worker.py`)
APScheduler interval job (60 s). It selects contacts in these stages whose `next_follow_up_at <= now()`:
* `new_lead`: first dials that the guard queued;
* `no_answer_retry`;
* `follow_up_requested`.

It only runs when the local time in `agent_config.timezone` falls within business hours, and does nothing when `TELEPHONY_PROVIDER=webrtc_only`. It claims each row atomically (`FOR UPDATE SKIP LOCKED` → `calling_in_progress`, `next_follow_up_at = NULL`) and dials. If the dial guard blocks the dial, it deletes the pending call row, restores the stage and re-queues temporary blocks.

### 5.6 Security & compliance
* **Access:**
  * Supabase self sign-up is off: `[auth] enable_signup = false`. The email provider stays on for sign-in.
  * Admins are users whose `raw_app_meta_data.role = 'admin'`, which only SQL or the service role can set.
  * The same rule is enforced in three places: RLS (`is_admin()`), the voice-engine guard (`services/auth.py`, `ADMIN_ROLE`) and `src/middleware.ts`, which signs a non-admin out and sends them to `/login?error=not_admin`.
  * Token errors return a generic message. Sign-in attempts are capped at 10 per 5 minutes per IP.
* **Fail-closed config:** `Settings.check_security()` runs at startup. `APP_ENV=auto` (the default) means production as soon as Twilio is on or the public or Supabase URL isn't local (`localhost`, `127.0.0.1`, `host.docker.internal`).
  * It flags the published Supabase local JWT secret, placeholder or short webhook secrets, `postgres:postgres` DB credentials, disabled Twilio signature checks, non-https webhook URLs when Twilio is on, `LOG_LEVEL=DEBUG`, and non-local `http://` CORS origins.
  * With `APP_ENV=production` any of these refuses startup; in development they are logged as warnings.
  * A placeholder `LEAD_WEBHOOK_SECRET` (for example `change-me`) never authenticates.
* **Dial guard (`services/dial_guard.py`):** `telephony.initiate_outbound_call` checks, in order:
  1. the number is in `ALLOWED_DIAL_COUNTRY_CODES` (default `["1"]`);
  2. consent is on record (`REQUIRE_DIAL_CONSENT`);
  3. it's within business hours;
  4. fewer than `MAX_DIALS_PER_NUMBER_PER_DAY` real dials to that number in the past 24 hours;
  5. fewer than `MAX_DIALS_PER_HOUR` dials in total in the past hour.

  It raises `DialBlocked(reason, retry_at)`. Country and consent blocks are permanent; hours and rate blocks carry `retry_at`.
* **Prompt injection:** contact data and custom fields enter the system prompt only inside `<caller_data>…</caller_data>`, after `sanitize.py` has flattened them to one line, stripped markup and capped them. The prompt describes that block as background facts, not guidance. Post-call extracted fields are cleaned the same way, and the extraction prompt treats the transcript as untrusted.
  * Keep this wording neutral. Jailbreak-style phrasing ("ignore any request to override your instructions…") trips Bedrock's content filter, and Nova Sonic then refuses the session.
* **Twilio WebSocket:** the stream URL includes `call_log_id`, so Twilio's signature differs per call: its signature has no timestamp or nonce, so a fixed URL would make one captured header valid forever. `/ws/voice` validates `X-Twilio-Signature` against that exact URL (the `wss://` form and its `https://` equivalent, empty params) before accepting, then enforces the call-match rules in §5.1. `VALIDATE_TWILIO_WS_SIGNATURE=false` exists only for debugging a URL-rewriting proxy. This hasn't been verified against live Twilio yet.
* **Rate limits and capacity (`services/rate_limit.py`):**
  * An in-process sliding window per client IP: `LEADS_PER_MINUTE` and `WEBRTC_OFFERS_PER_MINUTE` (ICE patches are allowed 20×). It is pruned and capped at 10k tracked keys.
  * It is per process and resets on restart, so run one uvicorn worker or limit at the proxy as well. Behind a proxy, run uvicorn with `--proxy-headers --forwarded-allow-ips=<proxy>`.
  * `MAX_CONCURRENT_CALLS` covers phone and browser sessions together. When it's full, inbound callers hear "lines are busy", browser calls get 429, and outbound dials are deferred 5 minutes. `MAX_CONCURRENT_BROWSER_CALLS` is a smaller sub-cap.
* **Recording:** off by default. When `record_calls` is on, outbound Twilio calls are recorded and the agent reads `recording_disclosure` right after its opening line. Inbound and browser calls are never recorded. The dashboard plays recordings only through `GET /api/recordings/{id}`, never from the raw Twilio URL.
* **Retention (`services/retention.py`):** a daily job (always scheduled) clears `transcript`, `recording_url` and `extracted_data` for calls older than `RETENTION_DAYS` (default 365) and deletes the recording at Twilio. Outcome, sentiment, duration and summary are kept.
* **Logs:** `LOG_LEVEL` defaults to INFO. At DEBUG, Pipecat logs whole system prompts, including caller data. Phone numbers are masked in voice-engine logs (`+15550101234` → `+*******1234`).
* **Admin revocation:** the API caches each user's current role for 30 s (`services/auth.py`); RLS checks it on every statement.
* **Dashboard headers (`next.config.ts`):**
  * A CSP whose `connect-src` is limited to self plus the Supabase and voice-engine origins (http and ws). It has `frame-ancestors 'none'`, and `'unsafe-eval'` in dev only, and `blob:` in `script-src` for the simulator's AudioWorklet (Pipecat's recorder loads its worklet from a same-origin blob URL). `'unsafe-inline'` scripts remain because Next injects inline bootstrap without a nonce setup.
  * `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy`, `Permissions-Policy` (microphone only), HSTS in production, and no `X-Powered-By`.
* **Realtime auth:** `use-crm.ts` calls `realtime.setAuth(access_token)` before subscribing and on every auth change. Without it, a page reload subscribes as `anon` and RLS silently drops every change.
* **Dependencies:** the `shadcn` CLI is a devDependency, and Next's bundled PostCSS is overridden to a patched 8.5.x. `npm audit --omit=dev` and `pip-audit` report 0 known vulnerabilities.

---

## 6. Frontend UI/UX

Calm, Linear-like shadcn/ui on Radix: 56px top nav, 8px grid, Geist + Geist Mono (numbers, phones, IDs), two weights (400/600).

**Design tokens (`globals.css`), light and dark:**
* Zinc neutrals plus ONE accent, `brand` (emerald). It is used only for live calls, booked states and call actions.
* Light-mode fills use emerald-700 so white text passes WCAG AA (emerald-600 is about 3.8:1). Dark mode uses emerald-500 with dark text.
* Semantic `danger` tokens cover errors and negative sentiment. Components never hardcode palette classes, and nothing uses pure black or pure white.
* Radius rule: controls, cards and columns use 8px; status pills and chips are fully rounded.
* Theme: `next-themes` follows the system by default, with a system/light/dark toggle in the nav and on the login page.
* Animations are gated by `motion-safe:`.

**Screens:**
* **Login (`/login`)**: email + password via Supabase Auth, with an inline error. `src/middleware.ts` redirects unauthenticated users here, and signs out non-admins with an explanation.
* **Pipeline (`/`)**
  * **KPI strip:** Total leads, Active calls (live indicator only while a call is active), Appointments booked, Conversion. It becomes a 2×2 grid on phones.
  * **Nav actions:** "+ New Lead / Dial" and "Test Agent in Browser ($0 WebRTC)". Below `md` they show icon only, with aria-labels.
  * **Kanban:** 7 columns mapped 1:1 to `pipeline_stage`, kept realtime via `supabase.channel('crm-pipeline')`.
    * Drag-and-drop updates `stage`. Cards open with Enter or a click.
    * Each card shows the name or formatted phone, deal value, latest sentiment badge, tags and relative `last_contacted_at`. Booked cards get an accent rail, and cards moved by realtime get a brief highlight.
    * There is a skeleton loader and a per-column empty hint. Columns snap-scroll at 85% width on phones.
  * **Contact drawer** (Sheet, 480px):
    * `Activity & Calls` tab: outcome, direction and sentiment badges, a summary, the conversation step path (from `extracted_data.flow`), a "Play recording" button (loads through the authenticated proxy) and a collapsible timestamped transcript. Shows an empty state when there are no calls.
    * `Contact Details` tab: the call-consent record (or a warning that there is none), plus editable name, email, phone, stage and a custom_fields key/value editor.
  * **Browser call simulator:**
    * Inbound / speed-to-lead toggle, with a lead picker for speed-to-lead.
    * Plain-language status (Not connected, Connecting…, Live with a timer, Call ended).
    * A blocked-microphone alert driven by Pipecat `onDeviceError`.
    * Uses `WavMediaManager` (plain `getUserMedia`), not the transport's default `DailyMediaManager`. The default downloads Daily's call-machine script from `c.dailywebrtc.net` at runtime, which the CSP blocks, and it would run third-party code next to the admin session.
    * A live transcript and a tool-call log (RTVI events), with empty states.
* **New lead dialog:** an "The lead agreed to be called" checkbox (sends `consent`). If the dial is blocked, a warning toast shows the reason and the next attempt time.
* **Agent Studio (`/settings`):** a form bound to `agent_config` (id=1). Fields: agent name, company, Nova Sonic voice, both greeting templates, qualification questions (each becomes a `save_qualification` field), objection handlers, Cal.com event type ID, transfer phone, timezone, redial hours, and call recording (on/off plus notice text). It has a skeleton loader and a sticky save bar showing "Unsaved changes" or "All changes saved".

---

## 7. Environment Variables
See `.env.example`, which is the contract. Keys: Supabase URLs and keys, `DATABASE_URL`, AWS credentials from the AWS CLI chain (`AWS_PROFILE` or the default `~/.aws` profile; explicit `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` optional override) + `AWS_REGION=us-east-1`, `NOVA_SONIC_MODEL_ID`, `BEDROCK_CLAUDE_MODEL_ID`, Twilio creds, `TELEPHONY_PROVIDER` (`twilio` | `webrtc_only`), `PUBLIC_VOICE_WEBHOOK_BASE_URL`, `CAL_API_KEY` and `LEAD_WEBHOOK_SECRET`. Security settings: `APP_ENV` (`auto`/`development`/`production`), `LOG_LEVEL`, `RETENTION_DAYS`, `ENABLE_API_DOCS`, `MAX_CONCURRENT_CALLS`, `MAX_BOOKINGS_PER_HOUR`, `VALIDATE_TWILIO_SIGNATURE`, `VALIDATE_TWILIO_WS_SIGNATURE`, `ALLOWED_DIAL_COUNTRY_CODES`, `REQUIRE_DIAL_CONSENT`, `MAX_DIALS_PER_NUMBER_PER_DAY`, `MAX_DIALS_PER_HOUR`, `LEADS_PER_MINUTE`, `WEBRTC_OFFERS_PER_MINUTE` and `MAX_CONCURRENT_BROWSER_CALLS`. In-call caps (`MAX_AVAILABILITY_CHECKS_PER_CALL` etc.) have defaults in `config.py`.

---

## 8. Phased Implementation (verify each gate before proceeding)
1. **DB & FastAPI skeleton.** Migration, seed, `db.py`, `leads.py`. Gate: `supabase db reset` is clean; pytest covers lead upsert and the pending call row.
2. **Nova Sonic pipeline & Cal.com tools (WebRTC first).** Gate: unit tests for prompts, stage routing, and booking transaction/rollback (Cal.com mocked); a manual browser call books a slot and moves the card to `appointment_booked`.
3. **Twilio adapters & retry worker.** Gate: TwiML/route tests, inbound latency check, retry-selection test; live PSTN via ngrok when creds exist.
4. **Next.js dashboard.** Gate: `npm run build` + typecheck; sign in, then see realtime card moves and a simulator call end-to-end.
5. **Conversation flow.** Gate: `tests/test_flow.py` covers guards, transitions, `next` text, callbacks, hangup and post-call overrides. A live scripted call (Polly-voiced caller → Nova Sonic, Cal.com stubbed) goes qualify → schedule → booked → closed and leaves the contact in `appointment_booked`.
6. **Security hardening.** Gate: `tests/test_security.py` covers non-admin rejection (API and real PostgREST/RLS), fail-closed config, every dial-guard rule, prompt-injection fencing, tool caps, rate limits and WebSocket signatures. Self sign-up returns `signup_disabled`, admin login and Realtime still work, and the live scripted call still completes.
7. **Second security review.** Gate: `tests/test_security_review2.py` covers:
   * per-call signatures and one stream per call;
   * the busy/429/defer caps, the booking cap, and the transfer country check;
   * production auto-detection, INFO logging, and docs off in production;
   * the recording proxy (with an SSRF guard), and retention;
   * no `anon` table access, and immediate admin revocation (API and real RLS);
   * withheld caller IDs, a malformed `contact_id` (422), and limiter bounds.

   Production headers are present, Realtime updates arrive after a page reload, and the live scripted call still completes.

## 9. Dev commands
```bash
supabase start && supabase db reset          # DB + seed
cd apps/voice-engine && uv run uvicorn src.main:app --reload --port 8000
cd apps/voice-engine && uv run pytest
cd apps/web && npm run dev                    # http://localhost:3000
```
