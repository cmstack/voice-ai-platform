-- Least privilege + immediate admin revocation (rev 5).
--
-- 1. Supabase grants every table privilege (incl. TRUNCATE, which RLS does not cover) to `anon`
--    and `authenticated` by default. The anonymous role needs none of them; signed-in users only
--    need row-level DML, still filtered by RLS.
-- 2. is_admin() now also checks the user's CURRENT role in auth.users, so removing an admin takes
--    effect on the next query instead of when their JWT expires (up to an hour).

REVOKE ALL ON agent_config, contacts, call_logs, appointments FROM anon;
REVOKE TRUNCATE, REFERENCES, TRIGGER ON agent_config, contacts, call_logs, appointments FROM authenticated;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public REVOKE ALL ON TABLES FROM anon;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public REVOKE TRUNCATE, REFERENCES, TRIGGER ON TABLES FROM authenticated;

CREATE OR REPLACE FUNCTION public.is_admin() RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = ''
AS $$
    SELECT coalesce(auth.jwt() -> 'app_metadata' ->> 'role', '') = 'admin'
       AND EXISTS (
           SELECT 1 FROM auth.users u
           WHERE u.id = auth.uid() AND u.raw_app_meta_data ->> 'role' = 'admin'
       )
$$;

REVOKE EXECUTE ON FUNCTION public.is_admin() FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.is_admin() TO authenticated;

-- Evaluate is_admin() once per statement (initplan) instead of once per row.
DROP POLICY IF EXISTS admin_all ON agent_config;
DROP POLICY IF EXISTS admin_all ON contacts;
DROP POLICY IF EXISTS admin_all ON call_logs;
DROP POLICY IF EXISTS admin_all ON appointments;
CREATE POLICY admin_all ON agent_config FOR ALL TO authenticated USING ((SELECT public.is_admin())) WITH CHECK ((SELECT public.is_admin()));
CREATE POLICY admin_all ON contacts FOR ALL TO authenticated USING ((SELECT public.is_admin())) WITH CHECK ((SELECT public.is_admin()));
CREATE POLICY admin_all ON call_logs FOR ALL TO authenticated USING ((SELECT public.is_admin())) WITH CHECK ((SELECT public.is_admin()));
CREATE POLICY admin_all ON appointments FOR ALL TO authenticated USING ((SELECT public.is_admin())) WITH CHECK ((SELECT public.is_admin()));
