"use client";

import React, { useEffect, useState } from "react";
import {
  Bot,
  ShieldCheck,
  Cpu,
  Database,
  CheckCircle2,
  Clock,
  AlertTriangle,
  Sparkles,
  X,
} from "lucide-react";
import OnboardingCard from "@/components/OnboardingCard";
import AgentControlBar from "@/components/AgentControlBar";
import ApplicationTracker from "@/components/ApplicationTracker";
import {
  ApplicationRecord,
  CandidateProfile,
  DEFAULT_USER_ID,
  QABankItem,
} from "@/lib/supabase";

const INITIAL_PROFILE: CandidateProfile = {
  id: DEFAULT_USER_ID,
  full_name: null,
  email: null,
  phone: null,
  location: "Remote",
  linkedin_url: null,
  portfolio_url: null,
  github_url: null,
  resume_url: null,
  parsed_cv_json: {
    skills: [],
    experience_years: 0,
    work_history: [],
    education: [],
    target_roles: [],
  },
  auto_apply_enabled: true,
  daily_apply_limit: 15,
  min_match_score: 75,
};

const INITIAL_QA_BANK: QABankItem[] = [];

const INITIAL_APPLICATIONS: ApplicationRecord[] = [];

export default function DashboardPage() {
  const [profile, setProfile] = useState<CandidateProfile>(INITIAL_PROFILE);
  const [qaBank, setQaBank] = useState<QABankItem[]>(INITIAL_QA_BANK);
  const [applications, setApplications] =
    useState<ApplicationRecord[]>(INITIAL_APPLICATIONS);
  const [isRunningCycle, setIsRunningCycle] = useState(false);
  const [statusBanner, setStatusBanner] = useState<string | null>(null);

  useEffect(() => {
    const fetchLiveDashboard = async () => {
      try {
        const res = await fetch(`/api/agent?user_id=${DEFAULT_USER_ID}`);
        if (!res.ok) return;
        const data = await res.json();
        if (data.profile) setProfile(data.profile);
        if (Array.isArray(data.qa_bank)) {
          setQaBank(data.qa_bank);
        }
        if (Array.isArray(data.applications)) {
          setApplications(data.applications);
        }
      } catch {
        // Fallback to seed state
      }
    };
    fetchLiveDashboard();
  }, []);

  const refreshDashboardFromBackend = async () => {
    try {
      const res = await fetch(`/api/agent?user_id=${profile.id}`);
      if (!res.ok) return;
      const data = await res.json();
      if (data.profile) setProfile(data.profile);
      if (Array.isArray(data.qa_bank)) {
        setQaBank(data.qa_bank);
      }
      if (Array.isArray(data.applications)) {
        setApplications(data.applications);
      }
    } catch {
      // Ignore network error
    }
  };

  const handleUploadSuccess = ({
    profile: updatedProf,
    gap_questions,
  }: {
    profile: Partial<CandidateProfile>;
    gap_questions: QABankItem[];
  }) => {
    setProfile((prev) => ({
      ...prev,
      ...updatedProf,
      parsed_cv_json: updatedProf.parsed_cv_json || prev.parsed_cv_json,
    }));
    if (gap_questions.length > 0) {
      setQaBank(gap_questions);
    }
    setStatusBanner(
      "CV parsed with local PII redaction, 384-d vector indexed, and gap questions generated."
    );
  };

  const handleSaveQAAnswers = async (
    answers: Array<{ question_key: string; answer_value: string }>
  ) => {
    setQaBank((prev) =>
      prev.map((item) => {
        const match = answers.find(
          (a) => a.question_key === item.question_key
        );
        return match
          ? { ...item, answer_value: match.answer_value, is_answered: true }
          : item;
      })
    );
    setApplications((prev) =>
      prev.map((app) =>
        app.status === "needs_human_input"
          ? { ...app, status: "queued", error_log: null }
          : app
      )
    );

    try {
      await fetch("/api/qa", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ user_id: profile.id, answers }),
      });
      setStatusBanner(
        `Saved ${answers.length} verified answer(s) to QA Bank and re-queued paused applications.`
      );
    } catch {
      // Optimistically updated
    }
  };

  const handleUpdateSettings = async (patch: {
    auto_apply_enabled?: boolean;
    min_match_score?: number;
    daily_apply_limit?: number;
    target_roles?: string[];
  }) => {
    setProfile((prev) => ({
      ...prev,
      auto_apply_enabled:
        patch.auto_apply_enabled ?? prev.auto_apply_enabled,
      min_match_score: patch.min_match_score ?? prev.min_match_score,
      daily_apply_limit: patch.daily_apply_limit ?? prev.daily_apply_limit,
      parsed_cv_json: {
        ...prev.parsed_cv_json,
        target_roles: patch.target_roles ?? prev.parsed_cv_json.target_roles,
      },
    }));

    try {
      await fetch("/api/agent", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ user_id: profile.id, ...patch }),
      });
    } catch {
      // Optimistically updated
    }
  };

  const handleTriggerRunNow = async () => {
    setIsRunningCycle(true);
    setStatusBanner(null);
    try {
      const res = await fetch("/api/agent", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          user_id: profile.id,
          run_discovery: true,
          run_auto_apply: true,
        }),
      });
      const data = await res.json();
      if (res.ok && data.discovery) {
        await refreshDashboardFromBackend();
        setStatusBanner(
          `Cycle Complete: ${data.discovery.jobs_fetched ?? 0} fetched • ${
            data.discovery.jobs_passed_stage1_vector ?? 0
          } passed pgvector (≥0.55) • ${
            data.discovery.newly_queued_count ?? 0
          } queued.`
        );
      } else {
        setStatusBanner(
          data.detail ||
            "Upload your PDF CV first so your profile exists in Supabase before running discovery."
        );
      }
    } catch {
      setStatusBanner(
        "Could not reach backend worker on :8000. Verify FastAPI is running."
      );
    } finally {
      setIsRunningCycle(false);
    }
  };

  // KPI Metrics
  const appliedCount = applications.filter((a) => a.status === "applied").length;
  const queuedCount = applications.filter((a) => a.status === "queued").length;
  const needsInputCount = applications.filter(
    (a) => a.status === "needs_human_input"
  ).length;
  const avgMatchScore =
    applications.length > 0
      ? Math.round(
          applications.reduce((acc, a) => acc + a.match_score, 0) /
            applications.length
        )
      : 0;

  return (
    <div className="min-h-screen flex flex-col">
      {/* Sticky Glass Header */}
      <header className="sticky top-0 z-30 border-b border-zinc-800/80 bg-zinc-950/80 backdrop-blur-xl">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-indigo-500 to-violet-600 flex items-center justify-center text-white shadow-lg shadow-indigo-500/20">
              <Bot className="w-5 h-5" />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h1 className="text-sm sm:text-base font-bold tracking-tight text-zinc-100">
                  Autonomous Job Agent
                </h1>
                <span className="px-2 py-0.5 rounded-full text-[10px] font-mono uppercase tracking-wider bg-indigo-500/15 text-indigo-300 border border-indigo-500/30">
                  v1.0
                </span>
              </div>
              <p className="text-[11px] text-zinc-400 hidden sm:block">
                Zero-Cost Multi-Model Router • pgvector Semantic Filter • Stealth Playwright
              </p>
            </div>
          </div>

          <div className="hidden md:flex items-center gap-2 text-[11px]">
            <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-zinc-900 border border-zinc-800 text-zinc-300">
              <Cpu className="w-3.5 h-3.5 text-indigo-400" />
              Gemini 3.5 + Groq 120B
            </span>
            <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-zinc-900 border border-zinc-800 text-zinc-300">
              <Database className="w-3.5 h-3.5 text-emerald-400" />
              pgvector 384-d
            </span>
            <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-zinc-900 border border-zinc-800 text-zinc-300">
              <ShieldCheck className="w-3.5 h-3.5 text-amber-400" />
              PII Redacted
            </span>
          </div>
        </div>
      </header>

      <main className="flex-1 max-w-7xl w-full mx-auto px-4 sm:px-6 lg:px-8 py-6 space-y-5">
        {/* Status Toast Banner */}
        {statusBanner && (
          <div className="bg-indigo-500/10 border border-indigo-500/30 text-indigo-200 px-4 py-2.5 rounded-xl text-xs font-medium flex items-center justify-between shadow-lg">
            <div className="flex items-center gap-2">
              <Sparkles className="w-4 h-4 text-indigo-400 shrink-0" />
              <span>{statusBanner}</span>
            </div>
            <button
              type="button"
              onClick={() => setStatusBanner(null)}
              className="text-indigo-300 hover:text-white p-1"
            >
              <X className="w-3.5 h-3.5" />
            </button>
          </div>
        )}

        {/* Top KPI Strip */}
        <section className="grid grid-cols-2 lg:grid-cols-4 gap-3.5">
          <div className="rounded-2xl bg-zinc-900/60 border border-zinc-800/80 px-4 py-3.5 flex items-center justify-between">
            <div>
              <span className="text-[11px] font-medium text-zinc-400">
                Applications Submitted
              </span>
              <div className="text-2xl font-bold text-zinc-100 font-mono mt-0.5">
                {appliedCount}
                <span className="text-xs font-normal text-zinc-500 ml-1.5">
                  / {profile.daily_apply_limit} daily cap
                </span>
              </div>
            </div>
            <div className="w-9 h-9 rounded-xl bg-emerald-500/10 border border-emerald-500/20 flex items-center justify-center text-emerald-400">
              <CheckCircle2 className="w-4 h-4" />
            </div>
          </div>

          <div className="rounded-2xl bg-zinc-900/60 border border-zinc-800/80 px-4 py-3.5 flex items-center justify-between">
            <div>
              <span className="text-[11px] font-medium text-zinc-400">
                Queued for Playwright
              </span>
              <div className="text-2xl font-bold text-zinc-100 font-mono mt-0.5">
                {queuedCount}
              </div>
            </div>
            <div className="w-9 h-9 rounded-xl bg-sky-500/10 border border-sky-500/20 flex items-center justify-center text-sky-400">
              <Clock className="w-4 h-4" />
            </div>
          </div>

          <div className="rounded-2xl bg-zinc-900/60 border border-zinc-800/80 px-4 py-3.5 flex items-center justify-between">
            <div>
              <span className="text-[11px] font-medium text-zinc-400">
                Paused (Needs Input)
              </span>
              <div className="text-2xl font-bold text-zinc-100 font-mono mt-0.5">
                {needsInputCount}
              </div>
            </div>
            <div className="w-9 h-9 rounded-xl bg-amber-500/10 border border-amber-500/20 flex items-center justify-center text-amber-400">
              <AlertTriangle className="w-4 h-4" />
            </div>
          </div>

          <div className="rounded-2xl bg-zinc-900/60 border border-zinc-800/80 px-4 py-3.5 flex items-center justify-between">
            <div>
              <span className="text-[11px] font-medium text-zinc-400">
                Avg Match Score
              </span>
              <div className="text-2xl font-bold text-indigo-400 font-mono mt-0.5">
                {avgMatchScore}%
              </div>
            </div>
            <div className="w-9 h-9 rounded-xl bg-indigo-500/10 border border-indigo-500/20 flex items-center justify-center text-indigo-400">
              <Sparkles className="w-4 h-4" />
            </div>
          </div>
        </section>

        {/* 1. Agent Control Bar */}
        <AgentControlBar
          autoApplyEnabled={profile.auto_apply_enabled}
          minMatchScore={profile.min_match_score}
          dailyApplyLimit={profile.daily_apply_limit}
          targetRoles={profile.parsed_cv_json?.target_roles || []}
          isRunningCycle={isRunningCycle}
          onUpdateSettings={handleUpdateSettings}
          onTriggerRunNow={handleTriggerRunNow}
        />

        {/* 2. Onboarding / Profile Card + Screening Q&A Memory Bank */}
        <OnboardingCard
          userId={profile.id}
          profile={profile}
          qaBank={qaBank}
          onUploadSuccess={handleUploadSuccess}
          onSaveAnswers={handleSaveQAAnswers}
        />

        {/* 3. Live Application Tracker Table */}
        <ApplicationTracker applications={applications} />
      </main>
    </div>
  );
}
