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

How it fits together, with diagrams of the call flows, is in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md);
the full specification is in [CLAUDE.md](CLAUDE.md).

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
- `anthropic.claude-opus-5-5` (post-call summaries and outcome classification)

Check access with:

```bash
aws bedrock get-foundation-model-availability --region us-east-1 --model-id amazon.nova-2-5-sonic
```

**Optional:**
- A [Cal.com](https://cal.com) API key and event type ID, to book real appointments
  ([setup steps](#5-connect-calcom-calendar-booking)).
- A Twilio account, a phone number, and a public tunnel such as ngrok, for real phone calls
  ([setup steps](#6-connect-twilio-real-phone-calls)).

---

## 2. First-time setup

### Start the database

From the repo root:

```bash
supabase start
```

The first run downloads Docker images, which takes a few minutes. It then applies the migrations
in `supabase/migrations/` and `supabase/seed.sql`. Supabase Studio is at http://127.0.0.1:54323.

Self sign-up is turned off. Only **admins** can use the dashboard and its API, meaning users whose
`app_metadata.role` is `admin`. The seed creates one local admin (see [Run it](#3-run-it)). To make
another user an admin, create them in Studio (Authentication → Add user), then run this in the SQL
editor:

```sql
update auth.users set raw_app_meta_data = raw_app_meta_data || '{"role":"admin"}' where email = 'you@example.com';
```

### Create the env files

The voice engine reads the root `.env`:

```bash
cp .env.example .env
```

Then fill in these values:

- `NEXT_PUBLIC_SUPABASE_ANON_KEY` and `SUPABASE_JWT_SECRET`: shown by `supabase status` as
  `ANON_KEY` and `JWT_SECRET`.
- `CAL_API_KEY`: optional, needed for real bookings (see [Connect Cal.com](#5-connect-calcom-calendar-booking)).
- `LEAD_WEBHOOK_SECRET`: 24+ random characters (`openssl rand -hex 24`), used by external lead
  forms. Placeholders like `change-me` are treated as unset, so the webhook stays disabled.

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
   - **Inbound call:** the agent answers like a receptionist.
   - **Speed-to-lead call:** pick a lead. The agent waits for your "Hello?" and then gives its
     opener.

   The dialog shows the call status and timer, the live transcript and every tool call. If the
   browser blocks your microphone, a warning appears instead of a silent call.
2. **Watch the board update.** When the agent books a slot, the card moves to
   **Appointment Booked** without a page refresh. When you hang up, post-call analysis writes the
   summary, sentiment and transcript. Click any card to see them.
3. **Add a lead.** Click **+ New Lead / Dial**. Tick **The lead agreed to be called** when they did:
   the agent never dials a real number without recorded consent. With
   `TELEPHONY_PROVIDER="webrtc_only"` the card is created but no phone call is placed; use the browser
   simulator to call instead.
4. **Tune the agent.** Open **Agent Studio** (`/settings`) to edit the greetings, Nova Sonic voice,
   qualification questions, objection handlers, Cal.com event type ID, transfer number and
   redial hours. New calls use the changes immediately.

The dashboard follows your system's light or dark mode. Use the monitor, sun or moon button in the top
bar to switch.

### How a call is structured

The agent follows fixed steps. The code tracks which step the call is in, rejects tools used out
of order, and tells the agent its next step in every tool result:

| Step | What the agent does | Tools it can use |
|---|---|---|
| Qualify | Asks your Agent Studio questions, one at a time | `save_qualification`, `check_calendar_availability` |
| Schedule | Asks which day suits the caller, offers up to 3 times, confirms name and email | `check_calendar_availability`, `book_calendar_appointment` |
| Booked | Confirms the time, says goodbye, hangs up | `end_call` |
| Callback | Confirms the agreed callback time, hangs up | `end_call`, or `check_calendar_availability` if they change their mind |

During qualify and schedule the agent can also use `schedule_callback`. At any live step it can use
`transfer_to_human`, or `end_call` (for example "not interested" or "wrong number").

Each qualification question in Agent Studio becomes a field of `save_qualification`. The answers
are saved to the contact's custom fields. Bookings only go through for a time the agent actually
offered and a complete email address.

After the call, what the steps recorded (booking, callback time, decline) decides the card's stage;
Claude's transcript analysis only adds the summary, sentiment and extra details. The contact drawer
shows the steps each call went through, for example Qualify › Schedule › Booked › Ended.

### Send leads from an external form

```bash
curl -X POST http://localhost:8000/api/leads \
  -H "Content-Type: application/json" -H "X-Webhook-Secret: $LEAD_WEBHOOK_SECRET" \
  -d '{"phone":"+17135550199","full_name":"Jane Doe","email":"jane@example.com","trigger_call_immediately":true,"consent":true,"consent_source":"website form, TCPA checkbox"}'
```

If the dial is blocked (no consent, outside calling hours, a dial limit, or a country outside the
allowlist), the response says why in `blocked_reason`. Temporary blocks are queued and retried
automatically at `next_attempt_at`.

---

## 5. Connect Cal.com (calendar booking)

Without Cal.com the agent still talks and qualifies, but every calendar lookup says "the calendar is
unavailable" and it offers a callback instead. With it, the agent reads your real availability and
books meetings mid-call.

> **Cal.com renamed "Event Types" to "Links"** in October 2026. In the sidebar, use **Links**, not
> **Events**: Events is a separate feature for one-off workshops and meetups that people RSVP to,
> and the agent can't book those. The API still calls Links "event types", which is why the
> settings below say *event type ID*.

1. **Create the Link the agent will book.** In Cal.com, open **Links** in the sidebar and create
   (or pick) the meeting, for example a 15-minute intro call. Keep its booking questions to the
   defaults (name, email, notes): the agent only collects those, so an extra *required* question
   makes every booking fail. Turn **Requires confirmation** off if you want bookings confirmed
   instantly.
2. **Find its event type ID.** Open the Link; the number at the end of the page address
   (for example `…/links/123456` or `…/event-types/123456`) is its ID. If the address has no
   number, list your Links with the API key from step 3 and copy the `id` of the one you want:

   ```bash
   curl -s "https://api.cal.com/v2/event-types" -H "Authorization: Bearer $CAL_API_KEY" -H "cal-api-version: 2024-06-14"
   ```
3. **Create an API key.** In Cal.com, open **Settings** (bottom of the sidebar) → **Developer** →
   **API Keys**, or go straight to `app.cal.com/settings/developer/api-keys`. Create a key, choose
   **Never expires** (an expired key silently turns calendar lookups off), and copy it. It starts
   with `cal_live_` and is shown only once. Treat it like a password.
4. **Add the key to `.env`** and restart the voice engine:

   ```ini
   CAL_API_KEY="cal_live_..."
   ```

   Self-hosting Cal.com? Also set `CAL_API_BASE_URL` to your instance's `/v2` API URL (the default
   is `https://api.cal.com/v2`).
5. **Set the event type and timezone in Agent Studio** (`/settings` → *Calendar, transfer & calling
   hours*): enter the event type ID, pick the timezone the agent should quote times in, and save.
6. **Check the key and event type work** before making a call. This asks Cal.com for open slots
   over the next week:

   ```bash
   curl -s "https://api.cal.com/v2/slots?eventTypeId=123456&start=$(date +%F)&end=$(date -v+7d +%F)&timeZone=America/Chicago" -H "Authorization: Bearer $CAL_API_KEY" -H "cal-api-version: 2024-09-04"
   ```

   You should get `"status":"success"` with dates and times. An authentication error means the key
   is wrong; an empty `data` object means the event type has no availability in that window. On
   Linux, use `$(date -d '+7 days' +%F)` for the end date.
7. **Test it end to end.** Open **Test Agent in Browser**, say you'd like to book, pick a day, accept
   a time, and give your name and email. The tool log shows `check_calendar_availability` and
   `book_calendar_appointment`, the card moves to **Appointment Booked**, and the booking appears in
   Cal.com with an email invite.

What the agent sends Cal.com: the attendee's name, email, and phone number, the timezone from Agent
Studio, and a short note on the caller's goals. It offers at most three times per lookup, books
only a time it offered on that call, and books at most one upcoming meeting per contact
(`MAX_UPCOMING_APPOINTMENTS_PER_CONTACT`) and 20 per hour overall (`MAX_BOOKINGS_PER_HOUR`).

---

## 6. Connect Twilio (real phone calls)

Twilio is optional: the browser simulator needs none of this. Connect it when you want the agent to
answer a real number and place speed-to-lead calls.

1. **Get a Twilio account and a voice number.** In the Twilio Console, buy a phone number with
   **Voice** capability (Phone Numbers → Buy a number). Trial accounts can only call verified
   numbers and play a trial message first, which confuses voicemail detection, so upgrade before
   testing outbound calls.
2. **Copy your credentials.** The **Account SID** and **Auth Token** are under *Account Info* on the
   Console home page.
3. **Give the voice engine a public HTTPS address.** Twilio has to reach it, so run a tunnel to the
   engine's port (use your own port if it isn't 8000):

   ```bash
   ngrok http 8000
   ```

   Copy the `https://…` forwarding URL. A new ngrok session gets a new URL unless you reserve one,
   and every step below that uses the URL must then be repeated.
4. **Fill in `.env`:**

   ```ini
   TELEPHONY_PROVIDER="twilio"
   TWILIO_ACCOUNT_SID="AC..."
   TWILIO_AUTH_TOKEN="..."
   TWILIO_PHONE_NUMBER="+17135550100"
   PUBLIC_VOICE_WEBHOOK_BASE_URL="https://your-tunnel.ngrok-free.app"
   ```

   Use the number in `+` international format, and paste the tunnel URL exactly as ngrok shows it,
   with no trailing slash. Every webhook and media stream is signature-checked against this URL, so
   any difference makes Twilio's requests fail with 403.
5. **Mind production mode.** Turning on Twilio switches the engine into production mode
   (`APP_ENV="auto"`), and it then refuses to start with the local development secrets. For a quick
   test on your own machine, set `APP_ENV="development"`. For anything real, replace the secrets as
   described in [Before exposing it](#8-before-exposing-it-beyond-your-machine).
6. **Restart the voice engine**, then check it's reachable through the tunnel:

   ```bash
   curl -s https://your-tunnel.ngrok-free.app/health
   ```

   It should return `{"ok":true}`.
7. **Point your number at the engine for inbound calls.** In the Console, open Phone Numbers →
   Manage → Active numbers → your number. Under *Voice Configuration*, set **A call comes in** to
   **Webhook**, `https://your-tunnel.ngrok-free.app/api/calls/inbound`, method **HTTP POST**, and
   save. You don't configure anything for outbound calls or status updates: the engine sets those
   URLs on each call it places.
8. **Lock down the account.** In the Console:
   - **Voice → Settings → Geo permissions:** allow only the countries you call (US and Canada by
     default, matching `ALLOWED_DIAL_COUNTRY_CODES`).
   - **Voice → Settings → General:** turn on **HTTP Basic Authentication for media access**, so
     recording links don't work for anyone who simply has the URL. The dashboard plays recordings
     through the voice engine, which signs in to Twilio for you.
9. **Set the transfer number** (optional) in Agent Studio → *Human transfer phone*, in `+`
   international format. It must be in an allowed country; otherwise the agent offers a callback
   instead of transferring.
10. **Test an inbound call.** Call your Twilio number and the agent greets you. A first-time caller
    gets a card in **New Lead**; after you hang up it moves to its next stage, with a summary and
    transcript in the contact drawer.
11. **Test an outbound call.** Click **+ New Lead / Dial**, enter a number you can answer, tick
    **The lead agreed to be called**, and keep **Call immediately** on. Your phone rings within a
    few seconds; after you say "Hello?", the agent gives its opener.

When a dial is held back, the toast and the API response say why (`blocked_reason`). The dial
guard only places a call when all of these hold:

- the number is in `ALLOWED_DIAL_COUNTRY_CODES` (US and Canada by default);
- the contact has consent on record;
- it's within the business hours set in Agent Studio (weekdays only);
- the number hasn't been dialed 3 times in the past 24 hours, fewer than 30 dials went out in the
  past hour, and fewer than `MAX_CONCURRENT_CALLS` (10) calls are live.

Calling hours and limits are temporary blocks: the dial is queued and retried at
`next_attempt_at`. Missing consent and a disallowed country are permanent until you fix them.
Unanswered calls and voicemails are retried up to 3 times, 4 hours apart. Extra inbound callers
beyond `MAX_CONCURRENT_CALLS` hear "all of our lines are busy".

**Recording** is off by default. If you turn it on in Agent Studio, outbound calls are recorded and
the agent reads the recording notice right after its opening line. Check the consent rules where
you and your callers are.

**If inbound calls fail:**

| Symptom | Likely cause |
| --- | --- |
| Twilio's call log shows 403 from `/api/calls/inbound` | `PUBLIC_VOICE_WEBHOOK_BASE_URL` doesn't exactly match the URL Twilio called (scheme, host, trailing slash), or the auth token is wrong |
| Twilio shows 502/timeout | The tunnel or engine isn't running, or the tunnel points at the wrong port |
| The call connects but stays silent | Check the engine log: a rejected media stream ("invalid X-Twilio-Signature") means the same URL mismatch; a Bedrock error means AWS access |
| You hear "all of our lines are busy" | `MAX_CONCURRENT_CALLS` is reached; hang up other calls or raise it |

---

## 7. Tests

The voice-engine tests use the local Supabase database, so run `supabase start` first:

```bash
uv run --directory apps/voice-engine pytest
```

Dashboard checks:

```bash
npm --prefix apps/web run typecheck && npm --prefix apps/web run lint && npm --prefix apps/web run build
```

---

## 8. Before exposing it beyond your machine

- Production mode switches on by itself (`APP_ENV="auto"`) as soon as Twilio is enabled or the public
  or Supabase URLs aren't local. You can also set `APP_ENV="production"` explicitly. The voice
  engine then refuses to start with any of these:
  - known default secrets or default database credentials;
  - disabled Twilio signature checks, or a non-https webhook URL;
  - `LOG_LEVEL=DEBUG`;
  - non-local `http://` CORS origins.

  It also hides `/docs` and `/openapi.json`.
- Keep `LOG_LEVEL="INFO"`. At DEBUG, Pipecat writes full prompts, including callers' personal
  details, to the logs.
- Set `RETENTION_DAYS` to your data policy. Older transcripts, recordings and extracted details are
  purged daily; outcomes and summaries are kept.
- Use a hosted Supabase project with its own JWT secret and keys. **Never run `supabase/seed.sql`
  against it**: it contains a known admin password.
- Create your admin user and grant the role (SQL above). Keep sign-up off.
- Generate a real `LEAD_WEBHOOK_SECRET`, and serve everything over HTTPS.
- Behind a reverse proxy, run uvicorn with `--proxy-headers --forwarded-allow-ips=<proxy ip>` so rate
  limits see real client IPs. Rate limits live in the engine's memory, so run a single worker or add
  limits at the proxy too.
- The dashboard sends a Content-Security-Policy that only allows connections to itself, your Supabase
  URL and your voice engine URL. Set `NEXT_PUBLIC_SUPABASE_URL` and `NEXT_PUBLIC_VOICE_ENGINE_URL`
  before `npm run build`, because the policy is fixed at build time.
- Review `ALLOWED_DIAL_COUNTRY_CODES`, the dial limits and consent capture with whoever owns
  compliance.

---

## 9. Docker (optional)

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
| "Microphone blocked" in the simulator | Allow microphone access for `localhost:3000` in your browser settings, or open the dashboard in regular Chrome or Safari. |
| Agent keeps asking for the email | The booking tool rejects incomplete addresses (for example a missing "at"). Spell it out clearly, including the part after the at sign. |
| Login says "This account doesn't have admin access" | Grant the role with the SQL in [First-time setup](#2-first-time-setup), then sign in again. |
| Voice engine refuses to start: "Refusing to start in production" | Fix each listed setting, or use `APP_ENV="development"` on your own machine. |
| "Lead created but not dialed" | The toast gives the reason: consent, calling hours, a dial limit or the country allowlist. Queued dials retry on their own. |
| Browser call fails with "too many browser calls in progress" | Hang up an open simulator call (limit `MAX_CONCURRENT_BROWSER_CALLS`). |
| Voice engine refuses to start right after setting a tunnel URL | A non-local webhook URL switches on production mode. Replace the local dev secrets, or for a quick local Twilio test set `APP_ENV="development"`. |
| Board stops updating live | Check the browser console for CSP errors (wrong `NEXT_PUBLIC_*` URLs at build time) and that you're signed in as an admin. |
| Simulator error "Failed to load call object bundle … dailywebrtc.net" | You're on an older build. The simulator now uses Pipecat's `WavMediaManager`, so it no longer downloads Daily's script (which the CSP blocks). Pull the latest code and restart the dashboard. |
| "Recording unavailable" in the drawer | Set `TWILIO_ACCOUNT_SID` and `TWILIO_AUTH_TOKEN` on the voice engine; it fetches recordings from Twilio for you. |
| Reset all local data | `supabase db reset` re-applies the migrations and seed. |
