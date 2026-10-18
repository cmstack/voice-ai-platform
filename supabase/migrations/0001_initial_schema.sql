CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- 1. Pipeline Stages Enum
CREATE TYPE pipeline_stage AS ENUM (
    'new_lead',
    'calling_in_progress',
    'no_answer_retry',
    'follow_up_requested',
    'appointment_booked',
    'transferred_to_human',
    'not_interested'
);

-- Shared updated_at trigger
CREATE OR REPLACE FUNCTION set_updated_at() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;

-- 2. Agent Configuration (single row)
CREATE TABLE agent_config (
    id INT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    agent_name VARCHAR(80) NOT NULL DEFAULT 'Alex',
    company_name VARCHAR(120) NOT NULL DEFAULT 'Apex Services',
    nova_voice_id VARCHAR(40) NOT NULL DEFAULT 'tiffany',
    outbound_greeting TEXT NOT NULL DEFAULT 'Hi {{full_name}}, this is {{agent_name}} calling from {{company_name}}. I saw you just requested info—do you have 60 seconds?',
    inbound_greeting TEXT NOT NULL DEFAULT 'Thanks for calling {{company_name}}, this is {{agent_name}}. How can I help you today?',
    qualification_questions JSONB NOT NULL DEFAULT '["What is your primary goal?", "What timeline are you working with?"]',
    objection_handlers JSONB NOT NULL DEFAULT '{"too_expensive": "We offer flexible tiers—our 15-minute intro call is solely to see if the ROI makes sense.", "busy": "Totally understand. I can lock in a 10-minute slot for Thursday or Friday—which works better?"}',
    human_transfer_phone VARCHAR(20),
    cal_event_type_id INT NOT NULL DEFAULT 123456,
    timezone VARCHAR(64) NOT NULL DEFAULT 'America/Chicago',
    business_hours_start SMALLINT NOT NULL DEFAULT 9 CHECK (business_hours_start BETWEEN 0 AND 23),
    business_hours_end SMALLINT NOT NULL DEFAULT 19 CHECK (business_hours_end BETWEEN 1 AND 24),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

INSERT INTO agent_config (id) VALUES (1) ON CONFLICT DO NOTHING;

CREATE TRIGGER agent_config_updated_at BEFORE UPDATE ON agent_config
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- 3. Contacts (CRM + pipeline card state)
CREATE TABLE contacts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    phone VARCHAR(20) UNIQUE NOT NULL,
    full_name VARCHAR(120),
    email VARCHAR(255),
    stage pipeline_stage NOT NULL DEFAULT 'new_lead',
    deal_value NUMERIC(10, 2) DEFAULT 0.00,
    retry_count INT NOT NULL DEFAULT 0,
    tags TEXT[] DEFAULT '{}',
    custom_fields JSONB DEFAULT '{}',
    last_contacted_at TIMESTAMPTZ,
    next_follow_up_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_contacts_stage ON contacts(stage);
CREATE INDEX idx_contacts_next_followup ON contacts(next_follow_up_at)
    WHERE stage IN ('no_answer_retry', 'follow_up_requested');

CREATE TRIGGER contacts_updated_at BEFORE UPDATE ON contacts
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- 4. Call Logs
CREATE TABLE call_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    contact_id UUID NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    provider_call_id VARCHAR(128) UNIQUE,   -- Twilio CallSid or WebRTC pc_id
    direction VARCHAR(10) NOT NULL CHECK (direction IN ('inbound', 'outbound', 'webrtc')),
    duration_seconds INT DEFAULT 0,
    recording_url TEXT,
    transcript JSONB DEFAULT '[]',          -- [{role, content, timestamp}]
    summary TEXT,
    outcome VARCHAR(50),                    -- booked | voicemail | follow_up_requested | transferred | not_interested
    sentiment VARCHAR(20),                  -- positive | neutral | negative
    extracted_data JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_call_logs_contact_id ON call_logs(contact_id, created_at DESC);

-- 5. Appointments
CREATE TABLE appointments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    contact_id UUID NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    call_log_id UUID REFERENCES call_logs(id) ON DELETE SET NULL,
    cal_booking_uid VARCHAR(128) UNIQUE,
    scheduled_at TIMESTAMPTZ NOT NULL,
    status VARCHAR(20) DEFAULT 'confirmed' CHECK (status IN ('confirmed', 'cancelled', 'rescheduled', 'completed')),
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_appointments_contact_upcoming ON appointments(contact_id, scheduled_at)
    WHERE status = 'confirmed';

-- Row Level Security: only the signed-in admin (authenticated role) may use the
-- dashboard APIs. The voice engine connects as `postgres` and bypasses RLS.
ALTER TABLE agent_config ENABLE ROW LEVEL SECURITY;
ALTER TABLE contacts ENABLE ROW LEVEL SECURITY;
ALTER TABLE call_logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE appointments ENABLE ROW LEVEL SECURITY;

CREATE POLICY authenticated_all ON agent_config FOR ALL TO authenticated USING (true) WITH CHECK (true);
CREATE POLICY authenticated_all ON contacts FOR ALL TO authenticated USING (true) WITH CHECK (true);
CREATE POLICY authenticated_all ON call_logs FOR ALL TO authenticated USING (true) WITH CHECK (true);
CREATE POLICY authenticated_all ON appointments FOR ALL TO authenticated USING (true) WITH CHECK (true);

-- Realtime: full old-row images so UPDATE payloads carry the previous stage
ALTER TABLE contacts REPLICA IDENTITY FULL;
ALTER TABLE call_logs REPLICA IDENTITY FULL;
ALTER TABLE appointments REPLICA IDENTITY FULL;

ALTER PUBLICATION supabase_realtime ADD TABLE contacts;
ALTER PUBLICATION supabase_realtime ADD TABLE call_logs;
ALTER PUBLICATION supabase_realtime ADD TABLE appointments;
