-- 011_user_login_events_rls.sql — security: codify the User Audit table's RLS.
--
-- user_login_events was created by hand in Supabase (not in migrations 001-010); its
-- policies lived only in the dashboard. This captures them in git, derived from the
-- confirmed live behavior on 2026-06-18:
--   * admin (Kelvin) reads the full login log; rank-and-file users read nothing
--   * any authenticated user records ONLY their own login (fire-and-forget insert
--     from logLoginEvent in deploy/index.html)
--
-- Authoritative + idempotent: drops EVERY existing policy on the table first (names
-- of the original hand-made policies aren't tracked), then recreates the canonical
-- two. Transactional — any failure rolls back, so it can't half-apply and lock out
-- the audit page or the login-capture insert.

BEGIN;

ALTER TABLE public.user_login_events ENABLE ROW LEVEL SECURITY;

-- Drop all existing policies on the table regardless of their current names.
DO $$
DECLARE p record;
BEGIN
  FOR p IN
    SELECT policyname FROM pg_policies
    WHERE schemaname = 'public' AND tablename = 'user_login_events'
  LOOP
    EXECUTE format('DROP POLICY IF EXISTS %I ON public.user_login_events', p.policyname);
  END LOOP;
END $$;

-- Admin-only read. The User Audit page is admin-gated in the UI; this enforces it at
-- the row layer so no other authenticated user can read the login log.
CREATE POLICY "admin read user_login_events" ON public.user_login_events
  FOR SELECT TO authenticated
  USING (auth.jwt() ->> 'email' = 'kelvin@impactoutsourcing.co.ke');

-- Each authenticated user records only their own login event.
CREATE POLICY "user inserts own login_event" ON public.user_login_events
  FOR INSERT TO authenticated
  WITH CHECK (auth.uid() = user_id);

COMMIT;

-- Note: the server pipeline uses the service_role key (bypasses RLS), so it is
-- unaffected. No anon policy: login events are written only after authentication.
