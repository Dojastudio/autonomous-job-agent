import { createClient } from "@supabase/supabase-js";

const supabaseUrl =
  process.env.NEXT_PUBLIC_SUPABASE_URL || "https://placeholder-project.supabase.co";
const supabaseAnonKey =
  process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY || "placeholder-anon-key";

export const supabase = createClient(supabaseUrl, supabaseAnonKey);

export const BACKEND_URL =
  process.env.NEXT_PUBLIC_BACKEND_URL || "http://localhost:8000";

// Default demo user UUID when running locally before auth session
export const DEFAULT_USER_ID = "00000000-0000-0000-0000-000000000001";

export type ApplicationStatus =
  | "queued"
  | "applying"
  | "applied"
  | "needs_human_input"
  | "failed"
  | "interview"
  | "rejected";

export interface ParsedCVJson {
  skills: string[];
  experience_years: number;
  work_history: Array<{
    company: string;
    role: string;
    duration: string;
    highlights?: string[];
  }>;
  education: Array<{
    institution: string;
    degree: string;
    year: string;
  }>;
  target_roles: string[];
  target_locations?: string[];
}

export interface CandidateProfile {
  id: string;
  full_name: string | null;
  email: string | null;
  phone: string | null;
  location: string | null;
  linkedin_url: string | null;
  portfolio_url: string | null;
  github_url: string | null;
  resume_url: string | null;
  parsed_cv_json: ParsedCVJson;
  auto_apply_enabled: boolean;
  daily_apply_limit: number;
  min_match_score: number;
}

export interface QABankItem {
  id?: string;
  user_id: string;
  question_key: string;
  question_label: string;
  answer_value: string;
  is_answered: boolean;
}

export interface DiscoveredJobNested {
  company_name: string;
  job_title: string;
  location: string;
  remote_type: string;
  source: string;
  apply_url: string;
}

export interface ApplicationRecord {
  id: string;
  user_id: string;
  job_id: string;
  match_score: number;
  match_reasoning: string | null;
  status: ApplicationStatus;
  tailored_cover_letter: string | null;
  submitted_answers_json: Record<string, string>;
  proof_screenshot_url: string | null;
  error_log: string | null;
  applied_at: string | null;
  created_at: string;
  jobs_discovered?: DiscoveredJobNested;
}
