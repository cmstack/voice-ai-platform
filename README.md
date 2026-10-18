# Voice Agent CRM

A self-hosted AI voice agent that answers inbound calls, places speed-to-lead outbound calls, books
appointments on Cal.com mid-call, and moves leads across a realtime Kanban CRM.

| Part | Stack | Folder |
|---|---|---|
| Voice engine | FastAPI + Pipecat + **Amazon Nova 2.5 Sonic** (speech-to-speech), Claude on Bedrock for post-call analysis | `apps/voice-engine` |
| Dashboard | Next.js 15 + shadcn/ui + Supabase Realtime | `apps/web` |
| Database | Local Supabase (Postgres, Auth, Realtime) via the Supabase CLI | `supabase/` |

You can run everything for **$0 in telephony costs**: the "Test Agent in Browser" button talks to the
agent over WebRTC with your microphone. Twilio is only needed for real phone calls.

The full system design lives in [CLAUDE.md](CLAUDE.md).

---

## 1. Prerequisites

| Tool | Version | Check |
|---|---|---|
| Docker Desktop (running) | any recent | `docker info` |
| Supabase CLI | 2.x | `supabase --version` |
| uv (Python manager) | 0.5+ | `uv --version` |
| Node.js + npm | Node 20+ | `node --version` |
| AWS CLI, configured | 2.x | `aws sts get-caller-identity` |

**AWS access.** The engine uses your AWS CLI credentials (the default profile, or `AWS_PROFILE`).
In **us-east-1**, your account needs Bedrock access to:

- `amazon.nova-2-5-sonic` (the realtime voice)
- `anthropic.claude-opus-5-5` (post-call summaries and stage routing)

Check access with:

```bash
aws bedrock get-foundation-model-availability --region us-east-1 --model-id amazon.nova-2-5-sonic
```

**Optional:**
- A [Cal.com](https://cal.com) API key and event type ID, to book real appointments.
- A Twilio account, a phone number and a public tunnel such as ngrok, for real phone calls.

---

## 2. First-time setup

### Start the database

From the repo root:

```bash
supabase start
```

The first run downloads Docker images, which takes a few minutes. It then applies
`supabase/migrations/0001_initial_schema.sql` and `supabase/seed.sql`. Supabase Studio is at
http://127.0.0.1:54323.

### Create the env files

The voice engine reads the root `.env`:

```bash
cp .env.example .env
```

Then fill in these values:

- `NEXT_PUBLIC_SUPABASE_ANON_KEY` and `SUPABASE_JWT_SECRET`: shown by `supabase status` as
  `ANON_KEY` and `JWT_SECRET`.
- `CAL_API_KEY`: optional, needed for real bookings.
- `LEAD_WEBHOOK_SECRET`: any random string, used by external lead forms.

Leave the AWS key lines commented out to use your AWS CLI profile.

The dashboard reads only its own folder, so copy the public variables there:

```bash
grep '^NEXT_PUBLIC_' .env > apps/web/.env.local
```

### Install dependencies

```bash
uv sync --directory apps/voice-engine
```

```bash
npm install --prefix apps/web
```

---

## 3. Run it

Use two terminals, both started from the repo root.

**Voice engine** (http://localhost:8000):

```bash
uv run --directory apps/voice-engine uvicorn src.main:app --reload --port 8000
```

**Dashboard** (http://localhost:3000):

```bash
npm --prefix apps/web run dev
```

Open http://localhost:3000 and sign in with the local admin account. The email and password are on the
second line of [supabase/seed.sql](supabase/seed.sql). They only exist in your local Supabase.

> **Port 8000 already in use?** Start the engine with another port, for example `--port 8010`. Then
> set `NEXT_PUBLIC_VOICE_ENGINE_URL="http://localhost:8010"` in both `.env` and
> `apps/web/.env.local`, and restart the dashboard.

---

## 4. Try it

1. **Talk to the agent in your browser.** Click **Test Agent in Browser ($0 WebRTC)** and allow
   microphone access. Use regular Chrome or Safari; embedded preview browsers often block the mic.
   - **Simulate inbound call:** the agent answers like a receptionist.
   - **Simulate speed-to-lead call:** pick a lead. The agent waits for your "Hello?" and then gives
     its opener.

   The dialog shows the live transcript and every tool call (qualification, calendar lookups, bookings,
   callbacks, transfers, hang-up).

   The agent follows fixed steps: qualify, then schedule, then wrap up. Tools called out of order are
   rejected, and each tool result tells the agent its next step. The steps each call went through appear
   in the contact drawer.
2. **Watch the board update.** When the agent books a slot, the card moves to
   **Appointment Booked** without a page refresh. When you hang up, post-call analysis writes the
   summary, sentiment and transcript. Click any card to see them.
3. **Add a lead.** Click **+ New Lead / Dial**. With `TELEPHONY_PROVIDER="webrtc_only"` the card is
   created but no phone call is placed; use the browser simulator to call instead.
4. **Tune the agent.** Open **Agent Studio** (`/settings`) to edit the greetings, Nova Sonic voice,
   qualification questions, objection handlers, Cal.com event type ID, transfer number and
   redial hours. New calls use the changes immediately.

### Send leads from an external form

```bash
curl -X POST http://localhost:8000/api/leads \
  -H "Content-Type: application/json" -H "X-Webhook-Secret: $LEAD_WEBHOOK_SECRET" \
  -d '{"phone":"+17135550199","full_name":"Jane Doe","email":"jane@example.com","trigger_call_immediately":true}'
```

---

## 5. Real phone calls (Twilio)

1. Expose the engine publicly with a tunnel:

   ```bash
   ngrok http 8000
   ```

2. In `.env`, set:
   - `TELEPHONY_PROVIDER="twilio"`
   - `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN` and `TWILIO_PHONE_NUMBER`
   - `PUBLIC_VOICE_WEBHOOK_BASE_URL`: your https tunnel URL

   Then restart the engine.
3. In the Twilio console, set your number's **"A call comes in"** webhook to
   `POST https://<tunnel>/api/calls/inbound`.
4. Outbound calls now dial for real. Unanswered calls and voicemails are retried automatically, up
   to 3 times and 4 hours apart, only during the business hours set in Agent Studio.

Twilio webhooks are signature-checked, so the tunnel URL must exactly match
`PUBLIC_VOICE_WEBHOOK_BASE_URL`.

---

## 6. Tests

The voice-engine tests use the local Supabase database, so run `supabase start` first:

```bash
uv run --directory apps/voice-engine pytest
```

Dashboard checks:

```bash
npm --prefix apps/web run typecheck && npm --prefix apps/web run lint && npm --prefix apps/web run build
```

---

## 7. Docker (optional)

`docker-compose.yml` runs the engine and dashboard in containers. The database still comes from
`supabase start`, and your `~/.aws` folder is mounted read-only for Bedrock credentials.

```bash
docker compose up --build
```

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| Browser call connects but the agent never speaks | Make sure the microphone is allowed. Check the engine log for `403 UnrecognizedClientException`; if you see it, run `aws sts get-caller-identity` and check Bedrock model access in us-east-1. |
| `No AWS credentials found` | Run `aws configure` (or `aws sso login`), or set `AWS_PROFILE` in `.env`. |
| Dashboard loads but the board is empty or the login fails | Run `supabase status` and check that `apps/web/.env.local` has the current anon key. |
| Calendar tool says it's unavailable | Set `CAL_API_KEY` in `.env` and a real Cal.com event type ID in Agent Studio. |
| Reset all local data | `supabase db reset` re-applies the migration and seed. |
