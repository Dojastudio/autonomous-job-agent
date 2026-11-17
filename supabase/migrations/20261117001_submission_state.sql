-- Kept separate: PostgreSQL must commit a new enum value before it is used.
ALTER TYPE public.application_status ADD VALUE IF NOT EXISTS 'submission_uncertain';
