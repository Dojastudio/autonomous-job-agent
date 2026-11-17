ALTER TABLE public.candidate_profiles ADD COLUMN IF NOT EXISTS embedding_version text;
ALTER TABLE public.jobs_discovered ADD COLUMN IF NOT EXISTS embedding_version text;
ALTER TABLE public.applications ADD COLUMN IF NOT EXISTS confirmation jsonb;
ALTER TABLE public.applications ADD COLUMN IF NOT EXISTS evidence_review_required boolean NOT NULL DEFAULT false;
ALTER TABLE public.applications ADD COLUMN IF NOT EXISTS required_question_keys text[] NOT NULL DEFAULT '{}';
UPDATE public.applications SET evidence_review_required = true WHERE status = 'applied' AND confirmation IS NULL;
UPDATE storage.buckets SET public = false WHERE id = 'proofs';
DROP POLICY IF EXISTS "Public read access for application proofs" ON storage.objects;
CREATE POLICY "Owners read application proofs" ON storage.objects FOR SELECT TO authenticated
USING (bucket_id = 'proofs' AND (storage.foldername(name))[1] = auth.uid()::text);

CREATE TABLE public.agent_runs (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 user_id uuid NOT NULL REFERENCES public.candidate_profiles(id) ON DELETE CASCADE,
 status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','completed','failed')),
 run_discovery boolean NOT NULL DEFAULT true,
 run_auto_apply boolean NOT NULL DEFAULT false,
 progress text NOT NULL DEFAULT 'Waiting for worker',
 result jsonb, error text, worker_token uuid,
 created_at timestamptz NOT NULL DEFAULT now(), started_at timestamptz,
 heartbeat_at timestamptz, finished_at timestamptz
);
CREATE UNIQUE INDEX one_active_run_per_candidate ON public.agent_runs(user_id) WHERE status IN ('queued','running');
CREATE TABLE public.application_attempts (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 application_id uuid NOT NULL REFERENCES public.applications(id) ON DELETE CASCADE,
 user_id uuid NOT NULL REFERENCES public.candidate_profiles(id) ON DELETE CASCADE,
 run_id uuid NOT NULL REFERENCES public.agent_runs(id),
 status text NOT NULL DEFAULT 'applying', confirmation jsonb, error text,
 started_at timestamptz NOT NULL DEFAULT now(), finished_at timestamptz
);
CREATE INDEX attempts_daily_quota ON public.application_attempts(user_id,started_at);
ALTER TABLE public.agent_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.application_attempts ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Owners read runs" ON public.agent_runs FOR SELECT TO authenticated USING (auth.uid() = user_id);
CREATE POLICY "Owners read attempts" ON public.application_attempts FOR SELECT TO authenticated USING (auth.uid() = user_id);
-- Clients may read results, but only the trusted API/worker may write automation state.
DROP POLICY IF EXISTS "Users can insert own applications" ON public.applications;
DROP POLICY IF EXISTS "Users can update own applications" ON public.applications;

CREATE FUNCTION public.claim_agent_run(p_token uuid) RETURNS SETOF public.agent_runs
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
BEGIN
 RETURN QUERY UPDATE agent_runs SET status='running',worker_token=p_token,started_at=now(),heartbeat_at=now()
 WHERE id=(SELECT id FROM agent_runs WHERE status='queued' ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1)
 RETURNING *;
END $$;

CREATE FUNCTION public.claim_application(p_run uuid,p_token uuid) RETURNS SETOF public.applications
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE r agent_runs; candidate candidate_profiles; a applications; used integer;
BEGIN
 SELECT * INTO r FROM agent_runs WHERE id=p_run AND status='running' AND worker_token=p_token FOR UPDATE;
 IF NOT FOUND THEN RETURN; END IF;
 SELECT * INTO candidate FROM candidate_profiles WHERE id=r.user_id FOR UPDATE;
 IF NOT candidate.auto_apply_enabled THEN RETURN; END IF;
 SELECT count(*) INTO used FROM application_attempts WHERE user_id=r.user_id AND started_at >= date_trunc('day',now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC';
 IF used >= candidate.daily_apply_limit THEN RETURN; END IF;
 SELECT * INTO a FROM applications WHERE user_id=r.user_id AND status='queued' ORDER BY match_score DESC FOR UPDATE SKIP LOCKED LIMIT 1;
 IF NOT FOUND THEN RETURN; END IF;
 UPDATE applications SET status='applying',error_log=NULL WHERE id=a.id RETURNING * INTO a;
 INSERT INTO application_attempts(application_id,user_id,run_id) VALUES(a.id,r.user_id,r.id);
 RETURN NEXT a;
END $$;

CREATE FUNCTION public.recover_stale_runs() RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE r agent_runs; recovered integer := 0;
BEGIN
 FOR r IN SELECT * FROM agent_runs WHERE status='running' AND heartbeat_at < now()-interval '10 minutes' FOR UPDATE SKIP LOCKED LOOP
  UPDATE applications SET status='submission_uncertain',error_log='Worker interrupted. Review employer confirmation before retrying.'
  WHERE id IN (SELECT application_id FROM application_attempts WHERE run_id=r.id AND status='applying');
  UPDATE application_attempts SET status='submission_uncertain',finished_at=now(),error='Worker interrupted' WHERE run_id=r.id AND status='applying';
  UPDATE agent_runs SET status='failed',error='Worker heartbeat expired',finished_at=now() WHERE id=r.id;
  recovered := recovered+1;
 END LOOP;
 RETURN recovered;
END $$;
REVOKE ALL ON FUNCTION public.claim_agent_run(uuid),public.claim_application(uuid,uuid),public.recover_stale_runs() FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.claim_agent_run(uuid),public.claim_application(uuid,uuid),public.recover_stale_runs() TO service_role;

CREATE TABLE public.worker_heartbeats (
 id text PRIMARY KEY,
 heartbeat_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE public.worker_heartbeats ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.agent_runs,public.application_attempts,public.worker_heartbeats FROM anon,authenticated;
GRANT SELECT ON public.agent_runs,public.application_attempts TO authenticated;
GRANT ALL ON public.agent_runs,public.application_attempts,public.worker_heartbeats TO service_role;
