-- ==============================================================================
-- Migration: 001_init.sql
-- Project: Autonomous Job Finder & Auto-Apply AI Agent
-- Description: Sets up pgvector, enums, core tables (candidate_profiles,
--              candidate_qa_bank, jobs_discovered, applications), HNSW vector
--              indexes, similarity RPC functions, Storage buckets, and RLS.
-- ==============================================================================

-- 1. Enable Required Extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "vector";

-- 2. Create Custom Enum Types
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'application_status') THEN
        CREATE TYPE application_status AS ENUM (
            'queued',
            'applying',
            'applied',
            'needs_human_input',
            'failed',
            'interview',
            'rejected'
        );
    END IF;
END$$;

-- 3. Create Table: candidate_profiles
CREATE TABLE IF NOT EXISTS public.candidate_profiles (
    id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    full_name TEXT,
    email TEXT,
    phone TEXT,
    location TEXT,
    linkedin_url TEXT,
    portfolio_url TEXT,
    github_url TEXT,
    resume_url TEXT,
    resume_raw_text TEXT,
    parsed_cv_json JSONB DEFAULT '{
        "skills": [],
        "experience_years": 0,
        "work_history": [],
        "education": [],
        "target_roles": []
    }'::jsonb,
    profile_embedding vector(384),
    auto_apply_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    daily_apply_limit INTEGER NOT NULL DEFAULT 15 CHECK (daily_apply_limit >= 1 AND daily_apply_limit <= 200),
    min_match_score INTEGER NOT NULL DEFAULT 75 CHECK (min_match_score >= 0 AND min_match_score <= 100),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 4. Create Table: candidate_qa_bank
CREATE TABLE IF NOT EXISTS public.candidate_qa_bank (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES public.candidate_profiles(id) ON DELETE CASCADE,
    question_key TEXT NOT NULL,
    question_label TEXT NOT NULL,
    answer_value TEXT DEFAULT '',
    is_answered BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_candidate_qa_user_key UNIQUE (user_id, question_key)
);

-- 5. Create Table: jobs_discovered
CREATE TABLE IF NOT EXISTS public.jobs_discovered (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    external_job_id TEXT NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('greenhouse', 'lever', 'ashby', 'remotive', 'arbeitnow', 'himalayas')),
    company_name TEXT NOT NULL,
    job_title TEXT NOT NULL,
    location TEXT DEFAULT 'Remote',
    remote_type TEXT DEFAULT 'unknown',
    apply_url TEXT NOT NULL,
    description_text TEXT NOT NULL,
    job_embedding vector(384),
    discovered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_jobs_source_external_id UNIQUE (source, external_job_id)
);

-- 6. Create Table: applications
CREATE TABLE IF NOT EXISTS public.applications (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES public.candidate_profiles(id) ON DELETE CASCADE,
    job_id UUID NOT NULL REFERENCES public.jobs_discovered(id) ON DELETE CASCADE,
    match_score INTEGER NOT NULL CHECK (match_score >= 0 AND match_score <= 100),
    match_reasoning TEXT,
    status application_status NOT NULL DEFAULT 'queued',
    tailored_cover_letter TEXT,
    submitted_answers_json JSONB DEFAULT '{}'::jsonb,
    proof_screenshot_url TEXT,
    error_log TEXT,
    applied_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_applications_user_job UNIQUE (user_id, job_id)
);

-- ==============================================================================
-- 7. INDEXES (Performance & pgvector HNSW Cosine Similarity)
-- ==============================================================================

-- HNSW Vector Indexes for fast 384-d cosine similarity lookups
CREATE INDEX IF NOT EXISTS idx_jobs_discovered_embedding_hnsw
    ON public.jobs_discovered
    USING hnsw (job_embedding vector_cosine_ops);

CREATE INDEX IF NOT EXISTS idx_candidate_profiles_embedding_hnsw
    ON public.candidate_profiles
    USING hnsw (profile_embedding vector_cosine_ops);

-- Standard B-Tree Indexes for high-frequency worker & dashboard queries
CREATE INDEX IF NOT EXISTS idx_candidate_qa_user_unanswered
    ON public.candidate_qa_bank (user_id, is_answered);

CREATE INDEX IF NOT EXISTS idx_jobs_discovered_source_time
    ON public.jobs_discovered (source, discovered_at DESC);

CREATE INDEX IF NOT EXISTS idx_applications_user_status
    ON public.applications (user_id, status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_applications_status_queued
    ON public.applications (status)
    WHERE status = 'queued';

-- ==============================================================================
-- 8. TRIGGERS (Automated updated_at maintenance)
-- ==============================================================================

CREATE OR REPLACE FUNCTION public.trigger_set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS set_candidate_profiles_updated_at ON public.candidate_profiles;
CREATE TRIGGER set_candidate_profiles_updated_at
    BEFORE UPDATE ON public.candidate_profiles
    FOR EACH ROW EXECUTE FUNCTION public.trigger_set_updated_at();

DROP TRIGGER IF EXISTS set_candidate_qa_bank_updated_at ON public.candidate_qa_bank;
CREATE TRIGGER set_candidate_qa_bank_updated_at
    BEFORE UPDATE ON public.candidate_qa_bank
    FOR EACH ROW EXECUTE FUNCTION public.trigger_set_updated_at();

DROP TRIGGER IF EXISTS set_applications_updated_at ON public.applications;
CREATE TRIGGER set_applications_updated_at
    BEFORE UPDATE ON public.applications
    FOR EACH ROW EXECUTE FUNCTION public.trigger_set_updated_at();

-- ==============================================================================
-- 9. STAGE-1 SEMANTIC VECTOR MATCHING RPC FUNCTION (Zero LLM Token Cost)
-- ==============================================================================
-- Computes cosine similarity (1 - cosine_distance) between a user's
-- profile_embedding and all jobs not yet applied to by that user.
-- Discards anything below `min_similarity` (default 0.55).

CREATE OR REPLACE FUNCTION public.match_jobs_for_candidate(
    p_user_id UUID,
    p_min_similarity FLOAT DEFAULT 0.55,
    p_limit INTEGER DEFAULT 50
)
RETURNS TABLE (
    job_id UUID,
    external_job_id TEXT,
    source TEXT,
    company_name TEXT,
    job_title TEXT,
    location TEXT,
    remote_type TEXT,
    apply_url TEXT,
    description_text TEXT,
    similarity FLOAT
)
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
    v_profile_embedding vector(384);
BEGIN
    SELECT cp.profile_embedding
    INTO v_profile_embedding
    FROM public.candidate_profiles cp
    WHERE cp.id = p_user_id;

    IF v_profile_embedding IS NULL THEN
        RETURN;
    END IF;

    RETURN QUERY
    SELECT
        jd.id AS job_id,
        jd.external_job_id,
        jd.source,
        jd.company_name,
        jd.job_title,
        jd.location,
        jd.remote_type,
        jd.apply_url,
        jd.description_text,
        (1 - (jd.job_embedding <=> v_profile_embedding))::FLOAT AS similarity
    FROM public.jobs_discovered jd
    WHERE jd.job_embedding IS NOT NULL
      AND NOT EXISTS (
          SELECT 1
          FROM public.applications app
          WHERE app.user_id = p_user_id
            AND app.job_id = jd.id
      )
      AND (1 - (jd.job_embedding <=> v_profile_embedding)) >= p_min_similarity
    ORDER BY jd.job_embedding <=> v_profile_embedding ASC
    LIMIT p_limit;
END;
$$;

-- ==============================================================================
-- 10. ROW LEVEL SECURITY (RLS) POLICIES
-- ==============================================================================

ALTER TABLE public.candidate_profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.candidate_qa_bank ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.jobs_discovered ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.applications ENABLE ROW LEVEL SECURITY;

-- candidate_profiles: Users can read/insert/update their own profile
DROP POLICY IF EXISTS "Users can view own profile" ON public.candidate_profiles;
CREATE POLICY "Users can view own profile"
    ON public.candidate_profiles FOR SELECT
    USING (auth.uid() = id);

DROP POLICY IF EXISTS "Users can insert own profile" ON public.candidate_profiles;
CREATE POLICY "Users can insert own profile"
    ON public.candidate_profiles FOR INSERT
    WITH CHECK (auth.uid() = id);

DROP POLICY IF EXISTS "Users can update own profile" ON public.candidate_profiles;
CREATE POLICY "Users can update own profile"
    ON public.candidate_profiles FOR UPDATE
    USING (auth.uid() = id)
    WITH CHECK (auth.uid() = id);

-- candidate_qa_bank: Users can manage their own screening Q&A bank
DROP POLICY IF EXISTS "Users can view own QA bank" ON public.candidate_qa_bank;
CREATE POLICY "Users can view own QA bank"
    ON public.candidate_qa_bank FOR SELECT
    USING (auth.uid() = user_id);

DROP POLICY IF EXISTS "Users can insert own QA bank" ON public.candidate_qa_bank;
CREATE POLICY "Users can insert own QA bank"
    ON public.candidate_qa_bank FOR INSERT
    WITH CHECK (auth.uid() = user_id);

DROP POLICY IF EXISTS "Users can update own QA bank" ON public.candidate_qa_bank;
CREATE POLICY "Users can update own QA bank"
    ON public.candidate_qa_bank FOR UPDATE
    USING (auth.uid() = user_id)
    WITH CHECK (auth.uid() = user_id);

DROP POLICY IF EXISTS "Users can delete own QA bank" ON public.candidate_qa_bank;
CREATE POLICY "Users can delete own QA bank"
    ON public.candidate_qa_bank FOR DELETE
    USING (auth.uid() = user_id);

-- jobs_discovered: Any authenticated user can read discovered jobs
DROP POLICY IF EXISTS "Authenticated users can view discovered jobs" ON public.jobs_discovered;
CREATE POLICY "Authenticated users can view discovered jobs"
    ON public.jobs_discovered FOR SELECT
    TO authenticated
    USING (true);

-- applications: Users can view and update their own applications
DROP POLICY IF EXISTS "Users can view own applications" ON public.applications;
CREATE POLICY "Users can view own applications"
    ON public.applications FOR SELECT
    USING (auth.uid() = user_id);

DROP POLICY IF EXISTS "Users can insert own applications" ON public.applications;
CREATE POLICY "Users can insert own applications"
    ON public.applications FOR INSERT
    WITH CHECK (auth.uid() = user_id);

DROP POLICY IF EXISTS "Users can update own applications" ON public.applications;
CREATE POLICY "Users can update own applications"
    ON public.applications FOR UPDATE
    USING (auth.uid() = user_id)
    WITH CHECK (auth.uid() = user_id);

-- ==============================================================================
-- 11. SUPABASE STORAGE BUCKETS (`resumes` and `proofs`) & STORAGE POLICIES
-- ==============================================================================

INSERT INTO storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
VALUES
    ('resumes', 'resumes', false, 10485760, ARRAY['application/pdf']),
    ('proofs', 'proofs', true, 10485760, ARRAY['image/png', 'image/jpeg', 'image/webp'])
ON CONFLICT (id) DO NOTHING;

-- Storage Policies for `resumes` bucket (private per user folder: `{user_id}/resume.pdf`)
DROP POLICY IF EXISTS "Users can upload own resume" ON storage.objects;
CREATE POLICY "Users can upload own resume"
    ON storage.objects FOR INSERT
    TO authenticated
    WITH CHECK (
        bucket_id = 'resumes'
        AND (storage.foldername(name))[1] = auth.uid()::text
    );

DROP POLICY IF EXISTS "Users can read own resume" ON storage.objects;
CREATE POLICY "Users can read own resume"
    ON storage.objects FOR SELECT
    TO authenticated
    USING (
        bucket_id = 'resumes'
        AND (storage.foldername(name))[1] = auth.uid()::text
    );

-- Storage Policies for `proofs` bucket (readable for dashboard proof verification)
DROP POLICY IF EXISTS "Public read access for application proofs" ON storage.objects;
CREATE POLICY "Public read access for application proofs"
    ON storage.objects FOR SELECT
    USING (bucket_id = 'proofs');
