-- Security hardening (rev 4).
--
-- 1. Admin-only data access. Signing in is not enough: the JWT must carry
--    app_metadata.role = 'admin'. app_metadata can only be written with the service
--    role or SQL, so a self-registered user can never grant it to themselves.
-- 2. Call-consent tracking on contacts (TCPA-style prior consent before auto-dialing).
-- 3. Call recording is opt-in, with a disclosure the agent speaks when it is on.

CREATE OR REPLACE FUNCTION public.is_admin() RETURNS boolean
LANGUAGE sql STABLE
AS $$
    SELECT coalesce(auth.jwt() -> 'app_metadata' ->> 'role', '') = 'admin'
$$;

DROP POLICY IF EXISTS authenticated_all ON agent_config;
DROP POLICY IF EXISTS authenticated_all ON contacts;
DROP POLICY IF EXISTS authenticated_all ON call_logs;
DROP POLICY IF EXISTS authenticated_all ON appointments;

CREATE POLICY admin_all ON agent_config FOR ALL TO authenticated USING (public.is_admin()) WITH CHECK (public.is_admin());
CREATE POLICY admin_all ON contacts FOR ALL TO authenticated USING (public.is_admin()) WITH CHECK (public.is_admin());
CREATE POLICY admin_all ON call_logs FOR ALL TO authenticated USING (public.is_admin()) WITH CHECK (public.is_admin());
CREATE POLICY admin_all ON appointments FOR ALL TO authenticated USING (public.is_admin()) WITH CHECK (public.is_admin());

ALTER TABLE contacts
    ADD COLUMN consent_at TIMESTAMPTZ,
    ADD COLUMN consent_source TEXT;

ALTER TABLE agent_config
    ADD COLUMN record_calls BOOLEAN NOT NULL DEFAULT false,
    ADD COLUMN recording_disclosure TEXT NOT NULL DEFAULT 'This call may be recorded for quality and training.';
