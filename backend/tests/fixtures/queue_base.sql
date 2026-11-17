-- Isolated queue integration fixture; Supabase/vector behavior is covered in staging.
DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='anon') THEN CREATE ROLE anon; END IF; END $$;
DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='authenticated') THEN CREATE ROLE authenticated; END IF; END $$;
DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='service_role') THEN CREATE ROLE service_role; END IF; END $$;
CREATE SCHEMA auth;
CREATE FUNCTION auth.uid() RETURNS uuid LANGUAGE sql AS $$ SELECT nullif(current_setting('request.jwt.claim.sub', true),'')::uuid $$;
CREATE SCHEMA storage;
CREATE TABLE storage.buckets(id text PRIMARY KEY,public boolean);
INSERT INTO storage.buckets VALUES ('proofs',true);
CREATE TABLE storage.objects(name text,bucket_id text);
CREATE FUNCTION storage.foldername(text) RETURNS text[] LANGUAGE sql AS $$ SELECT string_to_array($1,'/') $$;
CREATE TYPE application_status AS ENUM ('queued','applying','applied','needs_human_input','failed','interview','rejected');
CREATE TABLE candidate_profiles(id uuid PRIMARY KEY,auto_apply_enabled boolean DEFAULT false,daily_apply_limit integer DEFAULT 15);
CREATE TABLE jobs_discovered(id uuid PRIMARY KEY);
CREATE TABLE applications(id uuid PRIMARY KEY DEFAULT gen_random_uuid(),user_id uuid REFERENCES candidate_profiles(id),status application_status DEFAULT 'queued',match_score integer DEFAULT 80,error_log text);
